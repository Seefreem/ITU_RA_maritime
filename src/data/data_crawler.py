


import os
import json
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import unquote, urljoin, urlparse
from tqdm import tqdm

# =====================================================================
# CONFIGURATION
# =====================================================================
# The main search finder for Marine Accident Investigation Branch reports
BASE_SEARCH_URL = "https://www.gov.uk/maib-reports"

# Local directory where your downloaded PDFs will be saved
DOWNLOAD_DIR = os.path.join("data", "maib_reports_vault")

# Metadata file to store report information
METADATA_FILE = os.path.join(DOWNLOAD_DIR, "metadata.json")

# Local directory where your downloaded PDFs will be saved
DOWNLOAD_DIR = os.path.join(DOWNLOAD_DIR, "files")

# User-Agent header makes our script look like a standard web browser.
# This prevents GOV.UK's security firewalls from instantly blocking us.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Ensure the download folder exists before running
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def load_metadata():
    """
    Load existing metadata from JSON file.
    Returns: list of dict objects, each containing report metadata
    """
    if os.path.exists(METADATA_FILE):
        try:
            with open(METADATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            print(f"[Warning] Could not parse metadata file {METADATA_FILE}: {e}")
            return []
    return []


def save_metadata(metadata_list):
    """
    Save metadata list to JSON file.
    Args: metadata_list - list of dict objects with report metadata
    """
    with open(METADATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(metadata_list, f, indent=2, ensure_ascii=False)


def get_next_report_id(metadata_list):
    """
    Return the next numeric metadata ID.
    """
    numeric_ids = [
        entry.get("report_id")
        for entry in metadata_list
        if isinstance(entry.get("report_id"), int)
    ]
    return max(numeric_ids, default=0) + 1


def get_pdf_filename(pdf_url):
    """
    Extract a clean local PDF filename from the URL path.
    Query strings like ?__blob=publicationFile&v=1 are intentionally excluded.
    """
    parsed_url = urlparse(pdf_url)
    filename = unquote(os.path.basename(parsed_url.path))
    if not filename:
        # filename = "downloaded_report.pdf"
        return f"report_{int(time.time())}.pdf"
    return filename


def metadata_entry_exists(metadata_list, pdf_url):
    """
    Avoid duplicate metadata rows for the same PDF URL.
    """
    return any(entry.get("pdf_url") == pdf_url for entry in metadata_list)


def add_metadata_entry(pdf_url, report_webpage, report_title, pdf_filename):
    """
    Save metadata for a discovered PDF, even when the file was already downloaded.
    """
    metadata_list = load_metadata()
    if metadata_entry_exists(metadata_list, pdf_url):
        return

    metadata_entry = {
        "report_id": get_next_report_id(metadata_list),
        "report_webpage": report_webpage,
        "pdf_url": pdf_url,
        "pdf_filename": pdf_filename,
        "title": report_title
    }

    metadata_list.append(metadata_entry)
    save_metadata(metadata_list)


def extract_report_title(landing_page_url):
    """
    Extract the report title/slug from the landing page URL.
    Example: "https://www.gov.uk/maib-reports/safety-warning-issued-..." -> "safety-warning-issued-..."
    Returns: str - the slug portion after /maib-reports/
    """
    if "/maib-reports/" in landing_page_url:
        slug = landing_page_url.split("/maib-reports/")[-1].rstrip('/')
        return slug
    return ""


def extract_pdf_from_landing_page(landing_page_url):
    """
    Steps inside an individual report's landing page (e.g., /maib-reports/vessel-x-collision)
    and extracts PDF links.
    
    Returns: list of str - absolute URLs to PDF files found on the landing page
    """
    try:
        response = requests.get(landing_page_url, headers=HEADERS, timeout=15)
        # response.status_code: int, response.text: str (HTML content)
        if response.status_code != 200:
            return []
        
        soup = BeautifulSoup(response.text, 'html.parser')
        # soup: BeautifulSoup object with parsed HTML tree
        pdf_urls = []
        
        # Look for all anchor tags containing a link
        for anchor in soup.find_all('a', href=True):
            # anchor: Tag object, anchor['href']: str (href attribute)
            href = anchor['href']
            
            # GOV.UK stores report documents as PDF links.
            if ".pdf" in href.lower():
                # Resolve relative URLs to absolute URLs if necessary
                absolute_pdf_url = urljoin(landing_page_url, href)
                pdf_urls.append(absolute_pdf_url)
                
        return list(dict.fromkeys(pdf_urls)) # Remove duplicates while preserving order
        
    except Exception as e:
        print(f"   [Error] Could not read landing page {landing_page_url}: {e}")
        return []


def download_pdf_file(pdf_url, report_webpage, report_title):
    """
    Downloads the binary PDF file stream and streams it onto the local hard drive.
    Also saves metadata for the downloaded PDF.
    
    Args:
        pdf_url: str - URL of the PDF file to download
        report_webpage: str - URL of the report's landing page
        report_title: str - slug/title of the report
    """
    try:
        # Extract the original filename from the end of the URL string
        filename = get_pdf_filename(pdf_url)
        local_filepath = os.path.join(DOWNLOAD_DIR, filename)
        temp_filepath = f"{local_filepath}.part"
        
        # Skip downloading if the file already exists (safeguard against script interruptions)
        if os.path.exists(local_filepath):
            print(f"   [Skipped] {filename} already exists locally.")
            add_metadata_entry(pdf_url, report_webpage, report_title, filename)
            return

        print(f"   [Downloading] -> {filename}")
        
        # Use stream=True to download large PDFs efficiently without breaking RAM limits
        with requests.get(pdf_url, headers=HEADERS, stream=True, timeout=30) as r:
            # r.iter_content(chunk_size): Iterator[bytes] - yields chunks of response body
            r.raise_for_status()
            with open(temp_filepath, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            os.replace(temp_filepath, local_filepath)
        
        # Save metadata for this downloaded PDF
        add_metadata_entry(pdf_url, report_webpage, report_title, filename)
                    
        # Polite delay to honor server bandwidth rules
        time.sleep(1.5)
        
    except Exception as e:
        if 'temp_filepath' in locals() and os.path.exists(temp_filepath):
            os.remove(temp_filepath)
        print(f"   [Error] Failed to download binary asset {pdf_url}: {e}")


def main():
    """
    Primary execution loop. Traverses GOV.UK search finder pagination,
    discovers individual document layouts, and triggers downloading routines.
    """
    page_number = 1
    
    print("=========================================================")
    print(" STARTING BULK EXTRACTION: MAIB REPORT ARCHIVE ")
    print("=========================================================\n")
    
    while True:
        print(f"[*] Scanning Index Page {page_number}...")
        
        # Build the paginated URL parameter (e.g., https://www.gov.uk)
        target_index_url = f"{BASE_SEARCH_URL}?page={page_number}"
        
        try:
            response = requests.get(target_index_url, headers=HEADERS, timeout=15)
        except requests.RequestException as e:
            print(f"[!] Stopped. Could not read index page {page_number}: {e}")
            # break
            continue
        # response: requests.Response object; response.status_code: int; response.text: str (HTML content)
        
        # A 404 or non-200 code signals we have passed the final available page
        if response.status_code != 200:
            print(f"[!] Stopped. Server returned status code {response.status_code} on page {page_number}.")
            # break
            continue
            
        soup = BeautifulSoup(response.text, 'html.parser')
        # soup: BeautifulSoup object with parsed HTML tree
        
        # Target individual report landing links in GOV.UK's standard multi-page finder structure
        # They use 'gem-c-document-list__item-title' as class identifiers for links
        report_anchors = soup.select("li.gem-c-document-list__item a")
        # report_anchors: list of Tag objects
        
        # Alternate fallback locator structure in case the GOV.UK frontend updates classes
        if not report_anchors:
            report_anchors = soup.find_all('a', href=lambda href: href and "/maib-reports/" in href)

        # If no entries are found on the page at all, we've hit the empty tail end of the directory
        if not report_anchors:
            print("[+] Complete! No more report links found in the index.")
            break
            
        print(f" Found {len(report_anchors)} report entries on page {page_number}. Investigating profiles...")
        
        for anchor in tqdm(report_anchors, desc=f"Page {page_number} reports", leave=False):
            relative_href = anchor.get('href')
            # anchor['href']: str (href attribute value)
            
            # Ensure we are exploring actual report pathways, avoiding pagination/header links
            if not relative_href or "/maib-reports/" not in relative_href or "?page=" in relative_href:
                continue
                
            full_landing_url = urljoin("https://www.gov.uk", relative_href)
            
            # Extract report metadata
            report_title = extract_report_title(full_landing_url)
            
            # Extract underlying target PDF asset URLs nested inside that report's page
            pdf_targets = extract_pdf_from_landing_page(full_landing_url)
            
            # Download every file discovered on that report page
            for pdf_url in tqdm(pdf_targets, desc=f"Downloading {report_title}", leave=False):
                download_pdf_file(pdf_url, full_landing_url, report_title)
                
        # Advance pagination index counter
        page_number += 1
        if page_number > 30:  # Safety net to prevent infinite loops
            print("[!] Safety net triggered. Stopping pagination.")
            break
        print("-" * 50)
        
    print("\n=========================================================")
    print(f" PROCESSING COMPLETE. Files stored in: {DOWNLOAD_DIR}")
    print(f" Metadata saved to: {METADATA_FILE}")
    print("=========================================================")

if __name__ == "__main__":
    main()
