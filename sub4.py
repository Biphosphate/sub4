import csv
import requests
import time
import json
from typing import Dict, List, Optional
from itertools import islice

def parse_name(full_name: str) -> Dict[str, str]:
    """Parse full name into first and last name."""
    parts = full_name.strip().split()
    if len(parts) >= 2:
        return {
            'first_name': parts[0],
            'last_name': parts[-1]
        }
    elif len(parts) == 1:
        return {
            'first_name': '',
            'last_name': parts[0]
        }
    return {'first_name': '', 'last_name': ''}

def search_npi(first_name: str, last_name: str, skip: int = 0, limit: int = 10) -> Optional[Dict]:
    """
    Search NPI Registry for a physician by name.
    
    Args:
        first_name: Physician's first name
        last_name: Physician's last name
        skip: Number of results to skip
        limit: Maximum number of results to return
    
    Returns:
        JSON response from API or None if error
    """
    base_url = "https://npiregistry.cms.hhs.gov/api/"
    
    params = {
        'version': '2.1',
        'first_name': first_name,
        'last_name': last_name,
        'skip': skip,
        'limit': limit
    }
    
    try:
        response = requests.get(base_url, params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error searching for {first_name} {last_name}: {e}")
        return None

def extract_graduation_year(match: Dict) -> Optional[int]:
    """
    Try to extract graduation year from NPI data.
    Checks basic info and taxonomies for year information.
    """
    # Check taxonomies for license or other year info
    taxonomies = match.get('taxonomies', [])
    for tax in taxonomies:
        license_num = tax.get('license', '')
        # Some licenses contain years (this is a heuristic)
        if license_num:
            # Try to find a 4-digit year in the license
            import re
            years = re.findall(r'19\d{2}|20\d{2}', license_num)
            if years:
                year = int(years[0])
                if 1950 <= year <= 2025:  # Sanity check
                    return year
    
    # Check enumeration date as fallback (when they got NPI)
    # This is weaker but better than nothing
    basic = match.get('basic', {})
    enum_date = basic.get('enumeration_date', '')
    if enum_date:
        try:
            year = int(enum_date.split('-')[0])
            # NPI started in 2007, so subtract ~10 years for rough grad estimate
            if year >= 2007:
                return year - 10
        except:
            pass
    
    return None

def calculate_expected_grad_range(sub4_year: int) -> tuple:
    """
    Calculate plausible medical school graduation range based on sub-4 mile year.
    Assumes runner could be 18-28 years old, and graduates med school at 26-32 (typical timeline + some buffer).
    """
    min_grad = sub4_year - 2   # Ran at 28, graduated at 26
    max_grad = sub4_year + 6  # Ran at 18, graduated at 32 (with some delay)
    return (min_grad, max_grad)

def process_csv(csv_file: str, output_file: str = 'npi_results.json'):
    """
    Process CSV file and cross-reference with NPI Registry.
    
    Args:
        csv_file: Path to input CSV file
        output_file: Path to output JSON file with results
    """
    # Read CSV file
    try:
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            rows = list(reader)
    except FileNotFoundError:
        print(f"Error: Could not find {csv_file}")
        return
    except Exception as e:
        print(f"Error reading CSV: {e}")
        return
    
    # Check if rows exist and have at least 3 columns
    if not rows:
        print("Error: CSV file is empty")
        return
    
    # Get column 3 (index 2) from all rows, and column 1 (index 0) for year
    names = []
    years = []
    for row in rows:
        if len(row) >= 3:
            names.append(row[2])
            # Try to extract year from first column
            try:
                years.append(int(row[0]))
            except:
                years.append(None)
        else:
            names.append(None)
            years.append(None)
    
    results = []
    total_names = len(names)
    
    print(f"Processing {total_names} names from CSV...\n")
    
    for idx, (full_name, sub4_year) in enumerate(zip(names, years), 1):
        if not full_name or full_name.strip() == '':
            print(f"[{idx}/{total_names}] Skipping empty name")
            continue
        
        # Parse name
        name_parts = parse_name(full_name)
        first_name = name_parts['first_name']
        last_name = name_parts['last_name']
        
        # Calculate expected graduation range if we have the year
        expected_grad_range = None
        if sub4_year:
            expected_grad_range = calculate_expected_grad_range(sub4_year)
        
        print(f"[{idx}/{total_names}] Searching for: {full_name} (sub-4 year: {sub4_year})")
        
        # Search NPI Registry
        npi_data = search_npi(first_name, last_name)
        
        if npi_data and npi_data.get('result_count', 0) > 0:
            # Filter for exact matches only (must be male, individual, physician)
            exact_matches = []
            for match in npi_data.get('results', []):
                basic = match.get('basic', {})
                match_first = basic.get('first_name', '').lower()
                match_last = basic.get('last_name', '').lower()
                match_gender = basic.get('sex', '').upper()  # Field is 'sex' not 'gender'
                enumeration_type = match.get('enumeration_type', '')
                credential = basic.get('credential', '').upper()
                taxonomies = match.get('taxonomies', [])
                
                # Check if has physician taxonomy (code starts with 207, excludes 208 podiatrists)
                has_physician_taxonomy = any(
                    tax.get('code', '').startswith('207') 
                    for tax in taxonomies
                )
                
                # Must match name exactly, be male, individual, and either have MD/DO credential or physician taxonomy
                if (match_first == first_name.lower() and 
                    match_last == last_name.lower() and
                    match_gender == 'M' and
                    enumeration_type == 'NPI-1' and
                    (credential in ['MD', 'DO'] or has_physician_taxonomy)):
                    
                    # Check graduation year if we have expected range
                    if expected_grad_range:
                        grad_year = extract_graduation_year(match)
                        if grad_year:
                            min_grad, max_grad = expected_grad_range
                            if min_grad <= grad_year <= max_grad:
                                # Graduation year is within expected range
                                exact_matches.append(match)
                            else:
                                # Reject: graduation year doesn't match timeline
                                pass
                        else:
                            # No graduation year found, accept the match
                            exact_matches.append(match)
                    else:
                        # No sub-4 year available, accept the match
                        exact_matches.append(match)
            
            if exact_matches:
                match_count = len(exact_matches)
                
                # Only accept if there's exactly 1 match (reduces false positives)
                if match_count == 1:
                    print(f"  → Found 1 exact match (accepted)")
                    results.append({
                        'csv_name': full_name,
                        'first_name': first_name,
                        'last_name': last_name,
                        'sub4_year': sub4_year,
                        'result_count': 1,
                        'match_status': 'ACCEPTED',
                        'matches': exact_matches
                    })
                else:
                    # Multiple matches - flag for manual review
                    print(f"  → Found {match_count} exact matches (FLAGGED - multiple matches, manual review needed)")
                    results.append({
                        'csv_name': full_name,
                        'first_name': first_name,
                        'last_name': last_name,
                        'sub4_year': sub4_year,
                        'result_count': match_count,
                        'match_status': 'FLAGGED_MULTIPLE',
                        'matches': exact_matches
                    })
            else:
                print(f"  → No exact matches found")
                results.append({
                    'csv_name': full_name,
                    'first_name': first_name,
                    'last_name': last_name,
                    'sub4_year': sub4_year,
                    'result_count': 0,
                    'match_status': 'NO_MATCH',
                    'matches': []
                })
        else:
            print(f"  → No matches found")
            results.append({
                'csv_name': full_name,
                'first_name': first_name,
                'last_name': last_name,
                'sub4_year': sub4_year,
                'result_count': 0,
                'match_status': 'NO_MATCH',
                'matches': []
            })
        
        # Be respectful with API calls
        time.sleep(0.5)
    
    # Save results to JSON file
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"\n✓ Results saved to {output_file}")
    print(f"\nSummary:")
    print(f"  Total names processed: {len(results)}")
    accepted_count = sum(1 for r in results if r.get('match_status') == 'ACCEPTED')
    flagged_count = sum(1 for r in results if r.get('match_status') == 'FLAGGED_MULTIPLE')
    no_match_count = sum(1 for r in results if r.get('match_status') == 'NO_MATCH')
    print(f"  Accepted (single match): {accepted_count}")
    print(f"  Flagged (multiple matches): {flagged_count}")
    print(f"  No matches: {no_match_count}")
    
    # List accepted matches
    if accepted_count > 0:
        print(f"\n{'='*50}")
        print(f"ACCEPTED MATCHES ({accepted_count}):")
        print(f"{'='*50}")
        for r in results:
            if r.get('match_status') == 'ACCEPTED':
                print(f"  • {r['csv_name']} ({r.get('sub4_year', 'N/A')})")
    
    # List flagged matches that need review
    if flagged_count > 0:
        print(f"\n{'='*50}")
        print(f"FLAGGED FOR MANUAL REVIEW ({flagged_count}):")
        print(f"{'='*50}")
        for r in results:
            if r.get('match_status') == 'FLAGGED_MULTIPLE':
                print(f"  • {r['csv_name']} ({r.get('sub4_year', 'N/A')}) - {r['result_count']} matches")

def search_with_pagination(first_name: str, last_name: str, max_results: int = 50):
    """
    Search NPI Registry with pagination to get more results.
    
    Args:
        first_name: Physician's first name
        last_name: Physician's last name
        max_results: Maximum total results to retrieve
    """
    all_results = []
    skip = 0
    limit = 10
    
    while skip < max_results:
        print(f"Fetching results {skip} to {skip + limit}...")
        data = search_npi(first_name, last_name, skip=skip, limit=limit)
        
        if not data or data.get('result_count', 0) == 0:
            break
        
        results = data.get('results', [])
        all_results.extend(results)
        
        if len(results) < limit:
            break
        
        skip += limit
        time.sleep(0.1)
    
    return all_results

if __name__ == "__main__":
    # Main execution
    csv_file = "sub4_milers_full.csv"
    
    print("NPI Registry Cross-Reference Tool")
    print("=" * 50)
    
    # Process the CSV file
    process_csv(csv_file)
    
    # Example: Search with pagination for a specific name
    # results = search_with_pagination("John", "Smith", max_results=50)
    # print(f"Found {len(results)} total results with pagination")