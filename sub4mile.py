import re
import csv
import requests
import time
import json
from typing import Dict, List, Optional

# ============================================================================
# PART 1: DATA PARSING - Parse raw text data into structured CSV
# ============================================================================

def parse_miler_data_to_csv(data: str, output_csv: str = "sub4_milers_full.csv") -> str:
    """
    Parse raw text data about sub-4 minute milers and create a CSV file.
    
    Args:
        data: Raw text data containing miler information
        output_csv: Output CSV filename
    
    Returns:
        Path to created CSV file
    """
    lines = data.splitlines()
    entries = []
    current_year = None

    def normalize_whitespace(s):
        return re.sub(r'\s+', ' ', s.strip())

    for line in lines:
        line = normalize_whitespace(line)
        if not line:
            continue

        # Detect year headers
        if line.isdigit():
            current_year = line
            continue

        # Stage 1: Split rank from rest
        rank_split = re.match(r'^(\d+)\.\s*(.+)$', line)
        if not rank_split:
            print(f"Could not find rank: {line}")
            continue

        rank = rank_split.group(1)
        rest = rank_split.group(2)

        # Stage 2: Extract name and affiliation
        aff_match = re.search(r'\((.+?)\)', rest)
        if aff_match:
            aff_start, aff_end = aff_match.span()
            affiliation = aff_match.group(1).strip()
            name = rest[:aff_start].strip()
            rest_after_aff = rest[aff_end:].strip()
        else:
            affiliation = ""
            parts = rest.split()
            name = parts[0] + " " + " ".join(parts[1:-5])
            rest_after_aff = " ".join(parts[-5:])

        # Stage 3: Extract time
        time_match = re.search(r'([\d:.]+[*i]?)', rest_after_aff)
        if time_match:
            time_val = time_match.group(1)
            rest_after_time = rest_after_aff[time_match.end():].strip()
        else:
            print(f"No time found for line: {line}")
            continue

        # Stage 4: Extract race number
        race_match = re.search(r'\((\d+)\)', rest_after_time)
        if race_match:
            race_no = race_match.group(1)
            rest_after_race = rest_after_time[race_match.end():].strip()
        else:
            print(f"No race number found for line: {line}")
            continue

        # Stage 5: Extract day and month
        parts = rest_after_race.split()
        if len(parts) >= 2:
            day = parts[-1]
            month = parts[-2]
            location = " ".join(parts[:-2])
        else:
            print(f"Could not extract location/month/day for line: {line}")
            continue

        entries.append({
            "Year": current_year,
            "Rank": rank,
            "Name": name,
            "Affiliation": affiliation,
            "Time": time_val,
            "RaceNo": race_no,
            "Location": location,
            "Month": month,
            "Day": day
        })

    # Write CSV
    if entries:
        with open(output_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=entries[0].keys())
            writer.writeheader()
            for row in entries:
                writer.writerow(row)
        print(f"✓ CSV created with {len(entries)} entries: {output_csv}\n")
        return output_csv
    else:
        print("No entries parsed. Check your input.")
        return None


# ============================================================================
# PART 2: NPI CROSS-REFERENCE - Find which milers became physicians
# ============================================================================

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
    taxonomies = match.get('taxonomies', [])
    for tax in taxonomies:
        license_num = tax.get('license', '')
        if license_num:
            years = re.findall(r'19\d{2}|20\d{2}', license_num)
            if years:
                year = int(years[0])
                if 1950 <= year <= 2025:
                    return year
    
    # Check enumeration date as fallback
    basic = match.get('basic', {})
    enum_date = basic.get('enumeration_date', '')
    if enum_date:
        try:
            year = int(enum_date.split('-')[0])
            if year >= 2007:
                return year - 10
        except:
            pass
    
    return None


def calculate_expected_grad_range(sub4_year: int) -> tuple:
    """
    Calculate plausible medical school graduation range based on sub-4 mile year.
    Assumes runner could be 18-28 years old, and graduates med school at 26-32.
    """
    min_grad = sub4_year - 2
    max_grad = sub4_year + 6
    return (min_grad, max_grad)


def process_csv(csv_file: str, output_file: str = 'npi_results.json'):
    """
    Process CSV file and cross-reference with NPI Registry.
    
    Args:
        csv_file: Path to input CSV file
        output_file: Path to output JSON file with results
    """
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
    
    if not rows:
        print("Error: CSV file is empty")
        return
    
    # Get column 3 (name) and column 1 (year) from all rows
    names = []
    years = []
    for row in rows:
        if len(row) >= 3:
            names.append(row[2])
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
        
        name_parts = parse_name(full_name)
        first_name = name_parts['first_name']
        last_name = name_parts['last_name']
        
        expected_grad_range = None
        if sub4_year:
            expected_grad_range = calculate_expected_grad_range(sub4_year)
        
        print(f"[{idx}/{total_names}] Searching for: {full_name} (sub-4 year: {sub4_year})")
        
        npi_data = search_npi(first_name, last_name)
        
        if npi_data and npi_data.get('result_count', 0) > 0:
            exact_matches = []
            for match in npi_data.get('results', []):
                basic = match.get('basic', {})
                match_first = basic.get('first_name', '').lower()
                match_last = basic.get('last_name', '').lower()
                match_gender = basic.get('sex', '').upper()
                enumeration_type = match.get('enumeration_type', '')
                credential = basic.get('credential', '').upper()
                taxonomies = match.get('taxonomies', [])
                
                has_physician_taxonomy = any(
                    tax.get('code', '').startswith('207') 
                    for tax in taxonomies
                )
                
                if (match_first == first_name.lower() and 
                    match_last == last_name.lower() and
                    match_gender == 'M' and
                    enumeration_type == 'NPI-1' and
                    (credential in ['MD', 'DO'] or has_physician_taxonomy)):
                    
                    if expected_grad_range:
                        grad_year = extract_graduation_year(match)
                        if grad_year:
                            min_grad, max_grad = expected_grad_range
                            if min_grad <= grad_year <= max_grad:
                                exact_matches.append(match)
                        else:
                            exact_matches.append(match)
                    else:
                        exact_matches.append(match)
            
            if exact_matches:
                match_count = len(exact_matches)
                
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
                    print(f"  → Found {match_count} exact matches (FLAGGED - manual review needed)")
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
        
        time.sleep(0.5)
    
    # Save results to JSON
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
    
    if accepted_count > 0:
        print(f"\n{'='*50}")
        print(f"ACCEPTED MATCHES ({accepted_count}):")
        print(f"{'='*50}")
        for r in results:
            if r.get('match_status') == 'ACCEPTED':
                print(f"  • {r['csv_name']} ({r.get('sub4_year', 'N/A')})")
    
    if flagged_count > 0:
        print(f"\n{'='*50}")
        print(f"FLAGGED FOR MANUAL REVIEW ({flagged_count}):")
        print(f"{'='*50}")
        for r in results:
            if r.get('match_status') == 'FLAGGED_MULTIPLE':
                print(f"  • {r['csv_name']} ({r.get('sub4_year', 'N/A')}) - {r['result_count']} matches")


# ============================================================================
# MAIN EXECUTION
# ============================================================================

if __name__ == "__main__":
    print("="*70)
    print("SUB-4 MINUTE MILER TO PHYSICIAN CROSS-REFERENCE TOOL")
    print("="*70)
    print()
    
    # Step 1: Parse raw data (you would paste your raw data here)
    # For now, assuming CSV already exists or uncomment below to create it
    
    # raw_data = """your raw data here"""
    # csv_file = parse_miler_data_to_csv(raw_data)
    
    # Step 2: Cross-reference with NPI Registry
    csv_file = "sub4_milers_full.csv"
    
    print("STEP 1: Checking for CSV file...")
    try:
        with open(csv_file, 'r') as f:
            print(f"✓ Found {csv_file}\n")
    except FileNotFoundError:
        print(f"✗ CSV file not found. Please create it first or provide raw data.\n")
        exit(1)
    
    print("STEP 2: Cross-referencing with NPI Registry...")
    print("-" * 70)
    print()
    process_csv(csv_file)
    
    print("\n" + "="*70)
    print("COMPLETE")
    print("="*70)