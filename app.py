import ssl
import asyncio
from flask import Flask, render_template, request, session
from linkextractor import crawl_website
from filter_security_links import filter_security_links, filter_outdated_links
from ai_risk_analyzer import analyze_links
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# SSL workaround
try:
    ssl._create_default_https_context = ssl._create_unverified_context
except Exception as e:
    logging.error(f"SSL context setup failed: {e}")

app = Flask(__name__)
app.secret_key = 'your_secret_key'

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/crawl', methods=['POST'])
def crawl_website_route():
    seed_urls = request.form['seed_urls'].splitlines()
    domain = request.form['domain']
    include_subdomains = 'subdomains' in request.form
    expected_status = int(request.form['status_code'])
    depth = int(request.form['depth'])
    rate_limit = int(request.form['rate_limit'])
    use_gau = 'gau' in request.form
    use_wayback = 'wayback' in request.form
    filter_security = 'filter_security' in request.form
    filter_outdated = 'filter_outdated' in request.form

    # Ensure seed_urls is not empty
    if not seed_urls or all(not url.strip() for url in seed_urls):
        logging.error("No valid seed URLs provided")
        return render_template('error.html', message="Please provide at least one valid seed URL")

    try:
        links = asyncio.run(crawl_website(seed_urls, domain, include_subdomains, expected_status, 
                                         depth, rate_limit, use_gau, use_wayback, None))
        logging.info(f"Crawled {len(links)} links: {links}")
        
        # If no links are crawled, show a message
        if not links:
            logging.warning("No links were crawled from the provided seed URLs or tools")
            return render_template('results.html', links=[], security_matches={}, 
                                 message="No links found. Try adjusting depth, status code, or enabling external tools.")
    except Exception as e:
        logging.error(f"Crawling failed: {str(e)}")
        return render_template('error.html', message=f"Crawling failed: {str(e)}")

    # Apply filtering only if options are selected, otherwise keep all links
    filtered_links, security_matches = filter_links(links, filter_security, filter_outdated)
    logging.info(f"Processed {len(filtered_links)} links: {filtered_links}")
    
    session['extracted_links'] = filtered_links
    session['security_matches'] = security_matches
    
    return render_template('results.html', links=filtered_links, security_matches=security_matches)

def filter_links(links, filter_security, filter_outdated):
    """Filter links only if options are selected; otherwise return all links."""
    filtered = links  # Start with all crawled links
    security_matches = {}

    # Apply security filter only if selected
    if filter_security:
        filtered = filter_security_links(filtered)
        security_matches = {link: matches for link, matches in filtered}
        filtered = [link for link, _ in filtered]
    # Apply outdated filter only if selected
    if filter_outdated:
        filtered = filter_outdated_links(filtered)
    
    return filtered, security_matches

@app.route('/analyze')
def analyze_links_route():
    links = session.get('extracted_links', [])
    logging.info(f"Links to analyze: {len(links)} - {links}")
    if not links:
        return render_template('error.html', message="No links to analyze. Please crawl first.")
    
    analysis_results = analyze_links(links)
    logging.info(f"Raw analysis results: {len(analysis_results)} - {analysis_results}")
    
    filtered_analysis_results = [result for result in analysis_results if result['attack_type'] != 'None Detected']
    logging.info(f"Filtered analysis results: {len(filtered_analysis_results)} - {filtered_analysis_results}")
    
    if not filtered_analysis_results:
        return render_template('analyze.html', analysis_results=[], message="No risks detected in analyzed links.")
    
    return render_template('analyze.html', analysis_results=filtered_analysis_results)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000 ,debug=False)