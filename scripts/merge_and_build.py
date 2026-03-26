#!/usr/bin/env python3
"""
merge_and_build.py - Merge translated pages and build final outputs
Combines original steps 4-7: merge -> HTML -> TOC -> DOCX/EPUB/PDF

Usage: merge_and_build.py --temp-dir <path> [--title <title>] [--author <author>] [--lang <lang>]
"""

import os
import sys
import re
import glob
import shutil
import subprocess
import argparse
from pathlib import Path

from manifest import validate_for_merge

# Try to import BeautifulSoup for TOC generation
try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

# Try to import markdown
try:
    import markdown
    MARKDOWN_AVAILABLE = True
except ImportError:
    MARKDOWN_AVAILABLE = False

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# =============================================================================
# Language configuration — single source of truth for lang-dependent values
# =============================================================================

LANG_CONFIG = {
    'zh': {
        'lang_attr': 'zh-CN',
        'font_family': "'FangSong', '仿宋', 'STFangSong', '华文仿宋', serif",
        'font_family_ebook': '"FangSong", "FangSong_GB2312", "仿宋", "仿宋_GB2312", "STFangSong", "SimSun", serif',
        'toc_label': '目录',
        'pdf_font': 'FangSong',
    },
    'en': {
        'lang_attr': 'en',
        'font_family': "Georgia, 'Times New Roman', Times, serif",
        'font_family_ebook': 'Georgia, "Times New Roman", Times, serif',
        'toc_label': 'Contents',
        'pdf_font': 'Georgia',
    },
    'ja': {
        'lang_attr': 'ja',
        'font_family': "'Hiragino Mincho ProN', 'Yu Mincho', 'MS Mincho', serif",
        'font_family_ebook': '"Hiragino Mincho ProN", "Yu Mincho", "MS Mincho", serif',
        'toc_label': '目次',
        'pdf_font': 'Hiragino Mincho ProN',
    },
    'ko': {
        'lang_attr': 'ko',
        'font_family': "'Nanum Myeongjo', 'Batang', serif",
        'font_family_ebook': '"Nanum Myeongjo", "Batang", serif',
        'toc_label': '목차',
        'pdf_font': 'Nanum Myeongjo',
    },
    'fr': {
        'lang_attr': 'fr',
        'font_family': "Georgia, 'Times New Roman', Times, serif",
        'font_family_ebook': 'Georgia, "Times New Roman", Times, serif',
        'toc_label': 'Table des matières',
        'pdf_font': 'Georgia',
    },
    'de': {
        'lang_attr': 'de',
        'font_family': "Georgia, 'Times New Roman', Times, serif",
        'font_family_ebook': 'Georgia, "Times New Roman", Times, serif',
        'toc_label': 'Inhaltsverzeichnis',
        'pdf_font': 'Georgia',
    },
    'es': {
        'lang_attr': 'es',
        'font_family': "Georgia, 'Times New Roman', Times, serif",
        'font_family_ebook': 'Georgia, "Times New Roman", Times, serif',
        'toc_label': 'Índice',
        'pdf_font': 'Georgia',
    },
}

# Default fallback for unknown languages
_DEFAULT_LANG_CONFIG = {
    'lang_attr': 'en',
    'font_family': "Georgia, 'Times New Roman', Times, serif",
    'font_family_ebook': 'Georgia, "Times New Roman", Times, serif',
    'toc_label': 'Contents',
    'pdf_font': 'Georgia',
}


def get_lang_config(lang_code):
    """Get language config, falling back to defaults for unknown languages."""
    return LANG_CONFIG.get(lang_code, _DEFAULT_LANG_CONFIG)


def load_config(temp_dir):
    """Load configuration from config.txt"""
    config_file = os.path.join(temp_dir, 'config.txt')
    if not os.path.exists(config_file):
        print("Error: config.txt not found in temp directory.")
        sys.exit(1)

    config = {}
    with open(config_file, 'r', encoding='utf-8') as f:
        for line in f:
            if '=' in line and not line.strip().startswith('#'):
                key, value = line.strip().split('=', 1)
                config[key] = value
    return config


def natural_sort_key(text):
    """Natural sorting key for filenames with numbers"""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', text)]


# =============================================================================
# Step 4: Merge translated markdown files
# =============================================================================

def merge_markdown_files(temp_dir):
    """Merge all translated output files into output.md"""
    print("=== Merging translated markdown files ===")

    output_md = os.path.join(temp_dir, 'output.md')

    # Always validate manifest, even if output.md exists (catch stale/corrupt outputs)
    ok, ordered_files, warnings = validate_for_merge(temp_dir)

    if os.path.exists(output_md):
        if not ok:
            print(f"WARNING: output.md exists but manifest validation failed — deleting stale output.md")
            os.remove(output_md)
        else:
            # Check if any output_chunk is newer than output.md (re-translated chunks)
            output_md_mtime = os.path.getmtime(output_md)
            newer_chunks = []
            if ordered_files:
                newer_chunks = [
                    os.path.basename(f) for f in ordered_files
                    if os.path.getmtime(f) > output_md_mtime
                ]
            if newer_chunks:
                print(f"Re-merging — {len(newer_chunks)} chunk(s) newer than output.md: {', '.join(newer_chunks[:5])}{'...' if len(newer_chunks) > 5 else ''}")
                os.remove(output_md)
            else:
                print(f"Skipping merge - output.md already exists and is up to date")
                return True

    if not ok:
        print("ERROR: Merge validation failed. Fix the issues above before merging.")
        return False

    if ordered_files is not None:
        # Manifest-based merge
        print(f"Merging {len(ordered_files)} translated files (manifest-ordered)")
        merged_content = ""
        for file_path in ordered_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                if content:
                    merged_content += content + "\n\n"
            except Exception as e:
                print(f"Error reading {os.path.basename(file_path)}: {e}")
    else:
        # Legacy fallback: glob-based merge (no manifest)
        print("WARNING: No manifest.json found — using legacy glob-based merge.")
        print("  For hash validation, re-run convert.py to generate manifest.json")

        # Match chunk output files
        output_files = glob.glob(os.path.join(temp_dir, 'output_chunk*.md'))

        # Count original source files
        original_files = glob.glob(os.path.join(temp_dir, 'chunk*.md'))
        original_files = [f for f in original_files if not os.path.basename(f).startswith('output_')]

        if not output_files:
            print("Error: No translated markdown files found.")
            return False

        # Build expected output filename for each source file and verify 1:1 match
        source_basenames = sorted(
            [os.path.basename(f) for f in original_files],
            key=natural_sort_key
        )
        expected_outputs = set(f"output_{name}" for name in source_basenames)
        actual_outputs = set(os.path.basename(f) for f in output_files)

        missing = expected_outputs - actual_outputs
        orphaned = actual_outputs - expected_outputs

        if missing or orphaned:
            if missing:
                print(f"ERROR: Missing translations for: {', '.join(sorted(missing, key=natural_sort_key))}")
            if orphaned:
                print(f"ERROR: Orphaned outputs (no matching source): {', '.join(sorted(orphaned, key=natural_sort_key))}")
            return False

        # Verify no empty output files
        for fp in output_files:
            if os.path.getsize(fp) == 0:
                print(f"ERROR: Empty output file: {os.path.basename(fp)}")
                return False

        # Use source order to determine merge order (via expected output names)
        output_files = [
            os.path.join(temp_dir, f"output_{name}")
            for name in source_basenames
        ]
        print(f"Merging {len(output_files)} translated files (legacy glob)")

        merged_content = ""
        for file_path in output_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                if content:
                    merged_content += content + "\n\n"
            except Exception as e:
                print(f"Error reading {os.path.basename(file_path)}: {e}")

    try:
        with open(output_md, 'w', encoding='utf-8') as f:
            f.write(merged_content)
        file_size = os.path.getsize(output_md)
        print(f"Merged into output.md ({file_size:,} bytes)")
        return True
    except Exception as e:
        print(f"Error saving merged file: {e}")
        return False


# =============================================================================
# Step 5: Convert markdown to HTML
# =============================================================================

def check_pandoc_available():
    """Check if pandoc is available"""
    try:
        subprocess.run(['pandoc', '--version'], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def convert_with_pandoc(md_file, html_file, title, lang_attr):
    """Convert markdown to HTML using pandoc"""
    cmd = [
        'pandoc', md_file, '-o', html_file,
        '--standalone',
        '--metadata', f'title={title}',
        '--metadata', f'lang={lang_attr}',
        '--from', 'markdown+smart+east_asian_line_breaks',
        '--to', 'html5'
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        print("Converted with pandoc")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Pandoc conversion failed: {e.stderr}")
        return False


def convert_with_python_markdown(md_file, html_file, title):
    """Convert markdown to HTML using python-markdown (fallback 1)"""
    if not MARKDOWN_AVAILABLE:
        return False

    try:
        with open(md_file, 'r', encoding='utf-8') as f:
            md_content = f.read()

        extensions = ['toc', 'tables', 'fenced_code', 'codehilite', 'nl2br']
        md = markdown.Markdown(extensions=extensions)
        html_content = md.convert(md_content)

        full_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>{title}</title>
</head>
<body>
{html_content}
</body>
</html>"""

        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(full_html)

        print("Converted with python-markdown (fallback)")
        return True
    except Exception as e:
        print(f"python-markdown conversion failed: {e}")
        return False


def convert_with_basic_regex(md_file, html_file, title):
    """Convert markdown to HTML using basic regex (fallback 2)"""
    try:
        with open(md_file, 'r', encoding='utf-8') as f:
            md_content = f.read()

        html_content = md_content

        # Headers
        html_content = re.sub(r'^#### (.*?)$', r'<h4>\1</h4>', html_content, flags=re.MULTILINE)
        html_content = re.sub(r'^### (.*?)$', r'<h3>\1</h3>', html_content, flags=re.MULTILINE)
        html_content = re.sub(r'^## (.*?)$', r'<h2>\1</h2>', html_content, flags=re.MULTILINE)
        html_content = re.sub(r'^# (.*?)$', r'<h1>\1</h1>', html_content, flags=re.MULTILINE)

        # Bold and italic
        html_content = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', html_content)
        html_content = re.sub(r'\*(.*?)\*', r'<em>\1</em>', html_content)
        html_content = re.sub(r'_(.*?)_', r'<em>\1</em>', html_content)

        # Images
        html_content = re.sub(r'!\[([^\]]*)\]\(([^)]*)\)', r'<img src="\2" alt="\1">', html_content)

        # Links
        html_content = re.sub(r'\[([^\]]*)\]\(([^)]*)\)', r'<a href="\2">\1</a>', html_content)

        # Lists and paragraphs
        lines = html_content.split('\n')
        result_lines = []
        in_list = False

        for line in lines:
            stripped = line.strip()
            if stripped.startswith('- '):
                if not in_list:
                    result_lines.append('<ul>')
                    in_list = 'ul'
                item = stripped[2:]
                result_lines.append(f'<li>{item}</li>')
            elif re.match(r'^\d+\. ', stripped):
                if not in_list:
                    result_lines.append('<ol>')
                    in_list = 'ol'
                item = re.sub(r'^\d+\. ', '', stripped)
                result_lines.append(f'<li>{item}</li>')
            else:
                if in_list:
                    result_lines.append(f'</{in_list}>')
                    in_list = False
                if stripped and not stripped.startswith('<'):
                    result_lines.append(f'<p>{line}</p>')
                else:
                    result_lines.append(line)

        if in_list:
            result_lines.append(f'</{in_list}>')

        html_content = '\n'.join(result_lines)

        # Page separators
        html_content = re.sub(r'<p>---</p>', '<div class="page-separator"></div>', html_content)

        full_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>{title}</title>
</head>
<body>
{html_content}
</body>
</html>"""

        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(full_html)

        print("Converted with basic regex (fallback 2)")
        return True
    except Exception as e:
        print(f"Basic regex conversion failed: {e}")
        return False


def apply_template_to_html(html_content, template_file, output_file, title, lang_cfg, author=None):
    """Apply a template to HTML content with language-aware substitutions"""
    if not template_file or not os.path.exists(template_file):
        print(f"Warning: Template {template_file} not found")
        return False

    try:
        with open(template_file, 'r', encoding='utf-8') as f:
            template_content = f.read()

        if '$body$' in template_content:
            full_html = template_content.replace('$body$', html_content)
        elif '{{content}}' in template_content:
            full_html = template_content.replace('{{content}}', html_content)
        else:
            if '</body>' in template_content:
                full_html = template_content.replace('</body>', f'{html_content}\n</body>')
            else:
                full_html = template_content + html_content

        # Replace all template placeholders
        full_html = full_html.replace('$title$', title)
        full_html = full_html.replace('$lang$', lang_cfg['lang_attr'])
        full_html = full_html.replace('$body_font$', lang_cfg['font_family'])
        full_html = full_html.replace('$toc_label$', lang_cfg['toc_label'])

        # Inject author meta tag into <head> so calibre_html_publish.py can extract it
        if author:
            author_meta = f'<meta name="author" content="{author}">'
            if '<head>' in full_html or '<head ' in full_html:
                full_html = re.sub(
                    r'(<head[^>]*>)',
                    r'\1\n    ' + author_meta,
                    full_html,
                    count=1,
                    flags=re.IGNORECASE
                )

        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(full_html)
        return True
    except Exception as e:
        print(f"Error applying template: {e}")
        return False


def process_html_separators(html_file):
    """Process page separators in HTML"""
    try:
        with open(html_file, 'r', encoding='utf-8') as f:
            content = f.read()

        content = re.sub(r'<hr\s*/?>', '<div class="page-separator"></div>', content)
        content = re.sub(r'<p>\s*---\s*</p>', '<div class="page-separator"></div>', content)

        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        print(f"Error processing separators: {e}")


def convert_md_to_html(temp_dir, title, lang_cfg, author=None):
    """Convert output.md to HTML with templates"""
    print("=== Converting markdown to HTML ===")

    md_file = os.path.join(temp_dir, 'output.md')
    if not os.path.exists(md_file):
        print("Error: output.md not found.")
        return False

    book_doc_file = os.path.join(temp_dir, 'book_doc.html')

    # Skip HTML generation if book_doc.html exists and is newer than output.md
    if os.path.exists(book_doc_file):
        if os.path.getmtime(book_doc_file) > os.path.getmtime(md_file):
            print("Skipping HTML generation - book_doc.html is up to date")
            return True
        else:
            print("Re-generating HTML - output.md is newer")

    temp_html_file = os.path.join(temp_dir, 'output.html')

    # Try pandoc -> python-markdown -> basic regex
    success = False
    if check_pandoc_available():
        success = convert_with_pandoc(md_file, temp_html_file, title, lang_cfg['lang_attr'])

    if not success:
        success = convert_with_python_markdown(md_file, temp_html_file, title)

    if not success:
        success = convert_with_basic_regex(md_file, temp_html_file, title)

    if not success:
        print("Error: All markdown-to-HTML converters failed")
        return False

    process_html_separators(temp_html_file)

    # Extract body content
    try:
        with open(temp_html_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
    except Exception as e:
        print(f"Error reading HTML file: {e}")
        return False

    body_match = re.search(r'<body[^>]*>(.*?)</body>', html_content, re.DOTALL | re.IGNORECASE)
    body_content = body_match.group(1).strip() if body_match else html_content

    # Generate book_doc.html with ebook template
    template_ebook = os.path.join(SCRIPT_DIR, 'template_ebook.html')
    book_doc_file = os.path.join(temp_dir, 'book_doc.html')
    apply_template_to_html(body_content, template_ebook, book_doc_file, title, lang_cfg, author)

    # Generate book.html with web template
    template_web = os.path.join(SCRIPT_DIR, 'template.html')
    book_file = os.path.join(temp_dir, 'book.html')
    apply_template_to_html(body_content, template_web, book_file, title, lang_cfg, author)

    print(f"Generated: output.html, book_doc.html, book.html")
    return True


# =============================================================================
# Step 6: Add TOC
# =============================================================================

def generate_heading_id(text, existing_ids):
    """Generate unique ID for heading"""
    base_id = re.sub(r'[^\w\s-]', '', text.lower())
    base_id = re.sub(r'[-\s]+', '-', base_id)
    base_id = base_id.strip('-')

    if not base_id:
        base_id = 'heading'

    heading_id = base_id
    counter = 1
    while heading_id in existing_ids:
        heading_id = f"{base_id}-{counter}"
        counter += 1

    return heading_id


def generate_simple_toc_html(toc_data):
    """Generate simple HTML for table of contents"""
    if not toc_data:
        return ""

    toc_html = '<ul>\n'
    current_level = 1

    for item in toc_data:
        level = item['level']
        text = item['text']
        heading_id = item['id']

        if level > current_level:
            while current_level < level:
                toc_html += '<li><ul>\n'
                current_level += 1
        elif level < current_level:
            while current_level > level:
                toc_html += '</ul></li>\n'
                current_level -= 1

        toc_html += f'<li><a href="#{heading_id}">{text}</a></li>\n'

    while current_level > 1:
        toc_html += '</ul></li>\n'
        current_level -= 1

    toc_html += '</ul>\n'
    return toc_html


def insert_toc_with_bs4(html_file):
    """Insert TOC using BeautifulSoup"""
    try:
        with open(html_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
    except Exception as e:
        print(f"Error reading HTML file: {e}")
        return False

    soup = BeautifulSoup(html_content, 'html.parser')

    toc_data = []
    existing_ids = []

    for heading in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
        level = int(heading.name[1])
        text = heading.get_text().strip()
        if not text:
            continue

        heading_id = generate_heading_id(text, existing_ids)
        existing_ids.append(heading_id)
        heading['id'] = heading_id
        toc_data.append({'level': level, 'text': text, 'id': heading_id})

    if not toc_data:
        print("No headings found for TOC")
        return False

    toc_html = generate_simple_toc_html(toc_data)

    toc_content_div = soup.find('div', class_='toc-content')
    if toc_content_div:
        toc_content_div.clear()
        toc_soup = BeautifulSoup(toc_html, 'html.parser')
        toc_content_div.append(toc_soup)
        print(f"TOC inserted ({len(toc_data)} headings)")
    else:
        print("Warning: .toc-content div not found, TOC not inserted")
        return False

    try:
        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(str(soup))
        return True
    except Exception as e:
        print(f"Error saving HTML file: {e}")
        return False


def insert_toc_with_regex(html_file):
    """Insert TOC using regex (fallback)"""
    try:
        with open(html_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
    except Exception as e:
        print(f"Error reading HTML file: {e}")
        return False

    heading_pattern = r'<(h[1-6])(?:[^>]*)>(.*?)</\1>'
    headings = re.findall(heading_pattern, html_content, re.IGNORECASE | re.DOTALL)

    if not headings:
        print("No headings found for TOC")
        return False

    toc_html = '<ul>\n'
    for i, (tag, text) in enumerate(headings):
        level = int(tag[1])
        clean_text = re.sub(r'<[^>]+>', '', text).strip()
        heading_id = f"heading-{i+1}"

        old_heading = f'<{tag}>{text}</{tag}>'
        new_heading = f'<{tag} id="{heading_id}">{text}</{tag}>'
        html_content = html_content.replace(old_heading, new_heading, 1)

        if level > 1:
            for _ in range(level - 1):
                toc_html += '  '
        toc_html += f'<li><a href="#{heading_id}">{clean_text}</a></li>\n'

    toc_html += '</ul>\n'

    toc_content_pattern = r'(<div[^>]*class="toc-content[^"]*"[^>]*>).*?(</div>)'
    if re.search(toc_content_pattern, html_content, re.DOTALL):
        html_content = re.sub(
            toc_content_pattern,
            r'\1' + toc_html + r'\2',
            html_content,
            flags=re.DOTALL
        )
        print(f"TOC inserted ({len(headings)} headings)")
    else:
        print("Warning: .toc-content div not found")
        return False

    try:
        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(html_content)
        return True
    except Exception as e:
        print(f"Error saving HTML file: {e}")
        return False


def add_toc(temp_dir):
    """Add TOC to book.html"""
    print("=== Adding Table of Contents ===")

    book_file = os.path.join(temp_dir, 'book.html')
    if not os.path.exists(book_file):
        print("Warning: book.html not found, skipping TOC")
        return False

    if BS4_AVAILABLE:
        return insert_toc_with_bs4(book_file)
    else:
        return insert_toc_with_regex(book_file)


# =============================================================================
# Cover image extraction and embedding
# =============================================================================

def extract_cover_from_epub(epub_path, output_dir):
    """Extract cover image from an EPUB file using OPF metadata.

    Returns the path to the extracted cover image, or None if not found.
    """
    import zipfile
    import xml.etree.ElementTree as ET

    if not os.path.exists(epub_path):
        print(f"Cover source not found: {epub_path}")
        return None

    try:
        with zipfile.ZipFile(epub_path, 'r') as z:
            # Find OPF file
            opf_content = None
            opf_name = None
            for name in z.namelist():
                if name.endswith('.opf'):
                    opf_content = z.read(name)
                    opf_name = name
                    break

            if not opf_content or not opf_name:
                print("No OPF file found in EPUB")
                return None

            # Parse OPF XML to find cover image
            root = ET.fromstring(opf_content)
            ns = {'opf': 'http://www.idpf.org/2007/opf'}

            # Look for <item id="cover-image" ...> in <manifest>
            cover_href = None
            for item in root.findall('.//opf:manifest/opf:item', ns):
                if item.get('id') == 'cover-image':
                    cover_href = item.get('href')
                    break

            # Fallback: look for <meta name="cover" content="..."> then find that item
            if not cover_href:
                for meta in root.findall('.//opf:metadata/opf:meta', ns):
                    if meta.get('name') == 'cover':
                        cover_id = meta.get('content')
                        for item in root.findall('.//opf:manifest/opf:item', ns):
                            if item.get('id') == cover_id:
                                cover_href = item.get('href')
                                break
                        break

            if not cover_href:
                print("No cover-image metadata found in OPF")
                return None

            opf_dir = os.path.dirname(opf_name)
            cover_path = os.path.join(opf_dir, cover_href) if opf_dir else cover_href

            # Extract cover image
            os.makedirs(output_dir, exist_ok=True)
            z.extract(cover_path, output_dir)
            extracted_path = os.path.join(output_dir, cover_path)

            if os.path.exists(extracted_path):
                print(f"Extracted cover: {extracted_path} ({os.path.getsize(extracted_path):,} bytes)")
                return extracted_path
            else:
                print(f"Cover extraction failed: {cover_path}")
                return None

    except Exception as e:
        print(f"Error extracting cover: {e}")
        return None



# =============================================================================
# Step 7: Generate DOCX/EPUB/PDF with error transparency
# =============================================================================

def generate_format(html_file, temp_dir, output_ext, lang_attr, cover=None):
    """Generate a specific format using calibre_html_publish.py"""
    output_file = os.path.join(temp_dir, f"book{output_ext}")

    if os.path.exists(output_file):
        output_mtime = os.path.getmtime(output_file)

        # Check if source HTML is newer
        html_newer = os.path.getmtime(html_file) > output_mtime

        # Check if any image asset is newer (Calibre embeds these)
        images_newer = False
        images_dir = os.path.join(temp_dir, 'images')
        if os.path.isdir(images_dir):
            for img in os.listdir(images_dir):
                img_path = os.path.join(images_dir, img)
                if os.path.isfile(img_path) and os.path.getmtime(img_path) > output_mtime:
                    images_newer = True
                    break

        # Check if cover image is newer or newly added
        cover_newer = False
        if cover and os.path.isfile(cover):
            cover_newer = os.path.getmtime(cover) > output_mtime

        if not html_newer and not images_newer and not cover_newer:
            file_size = os.path.getsize(output_file)
            print(f"Skipping {output_ext} - already exists and up to date ({file_size:,} bytes)")
            return output_file
        else:
            reasons = []
            if html_newer:
                reasons.append("source HTML changed")
            if images_newer:
                reasons.append("image assets changed")
            if cover_newer:
                reasons.append("cover image changed")
            print(f"Rebuilding {output_ext} - {', '.join(reasons)}")

    publish_script = os.path.join(SCRIPT_DIR, "calibre_html_publish.py")
    if not os.path.exists(publish_script):
        print(f"calibre_html_publish.py not found at: {publish_script}")
        return None

    try:
        cmd = ["python3", publish_script, html_file, "-o", output_file, "--lang", lang_attr]
        if cover:
            cmd.extend(["--cover", cover])
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)

        if os.path.exists(output_file):
            file_size = os.path.getsize(output_file)
            return output_file
        else:
            print(f"Failed to generate {output_ext}")
            if result.stdout:
                print(f"  stdout: {result.stdout[-500:]}")
            return None
    except subprocess.CalledProcessError as e:
        print(f"Failed to generate {output_ext}")
        if e.stdout:
            print(f"  stdout: {e.stdout[-500:]}")
        if e.stderr:
            print(f"  stderr: {e.stderr[-500:]}")
        return None
    except Exception as e:
        print(f"Error generating {output_ext}: {e}")
        return None


def generate_formats(temp_dir, lang_attr, cover=None):
    """Generate DOCX, EPUB, and PDF with result summary"""
    print("=== Generating output formats ===")

    html_file = os.path.join(temp_dir, "book_doc.html")
    if not os.path.exists(html_file):
        html_files = glob.glob(os.path.join(temp_dir, "*.html"))
        if html_files:
            html_file = max(html_files, key=os.path.getmtime)
        else:
            print("No HTML files found for format generation")
            return

    results = {}
    for ext in ['.docx', '.epub', '.pdf']:
        # Pass cover only for EPUB format
        fmt_cover = cover if ext == '.epub' else None
        result = generate_format(html_file, temp_dir, ext, lang_attr, fmt_cover)
        if result:
            file_size = os.path.getsize(result)
            results[ext] = ('OK', f"{file_size:,} bytes")
        else:
            results[ext] = ('FAILED', '')

    # Print summary table
    print("\nFormat results:")
    has_failures = False
    for ext, (status, detail) in results.items():
        if status == 'OK':
            print(f"  {ext}: {status} ({detail})")
        else:
            print(f"  {ext}: {status}")
            has_failures = True

    return not has_failures


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description='Merge translated pages and build final outputs')
    parser.add_argument('--temp-dir', required=True, help='Temp directory path')
    parser.add_argument('--title', default=None, help='Translated book title (override config)')
    parser.add_argument('--author', default=None, help='Author name (override config)')
    parser.add_argument('--lang', default=None, help='Output language code (override config)')
    parser.add_argument('--cleanup', action='store_true', help='Remove intermediate artifacts after successful build')
    parser.add_argument('--cover', default=None, help='Cover image path to embed in EPUB')
    parser.add_argument('--cover-from', default=None, help='Extract cover from original EPUB file')

    args = parser.parse_args()
    temp_dir = args.temp_dir

    if not os.path.isdir(temp_dir):
        print(f"Error: Temp directory not found: {temp_dir}")
        sys.exit(1)

    # Load config as base, CLI args override
    config = load_config(temp_dir)

    lang_code = args.lang or config.get('output_lang', 'zh')
    lang_cfg = get_lang_config(lang_code)

    title = args.title or config.get('original_title', 'Translated Book')
    author = args.author or config.get('creator', 'Unknown Author')

    print(f"=== Merge and Build ===")
    print(f"Temp directory: {temp_dir}")
    print(f"Title: {title}")
    print(f"Author: {author}")
    print(f"Language: {lang_code} (attr: {lang_cfg['lang_attr']})")

    # Step 4: Merge
    if not merge_markdown_files(temp_dir):
        sys.exit(1)

    # Step 5: Convert to HTML
    if not convert_md_to_html(temp_dir, title, lang_cfg, author):
        sys.exit(1)

    # Step 6: Add TOC
    add_toc(temp_dir)

    # Resolve cover image before generating formats
    cover_image = args.cover  # explicit path
    if not cover_image and args.cover_from:
        print("\n=== Extracting cover from original EPUB ===")
        cover_image = extract_cover_from_epub(
            args.cover_from,
            os.path.join(temp_dir, 'cover_extract')
        )

    # Step 7: Generate formats (cover is passed to EPUB via Calibre --cover)
    all_formats_ok = generate_formats(temp_dir, lang_cfg['lang_attr'], cover_image)

    print("\n=== Build Complete ===")
    print(f"All outputs saved to: {temp_dir}")

    # List generated files
    for ext in ['book.html', 'book_doc.html', 'book.docx', 'book.epub', 'book.pdf']:
        filepath = os.path.join(temp_dir, ext)
        if os.path.exists(filepath):
            size = os.path.getsize(filepath)
            print(f"  {ext}: {size:,} bytes")

    # Cleanup intermediate artifacts if requested (skip if any format failed)
    if args.cleanup:
        if all_formats_ok:
            cleanup_intermediate_files(temp_dir)
        else:
            print("\nSkipping cleanup — some formats failed. Intermediate files kept for diagnosis/retry.")


def cleanup_intermediate_files(temp_dir):
    """Remove intermediate artifacts, keeping only final outputs."""
    print("\n=== Cleaning up intermediate files ===")

    removed = []

    # Remove chunk*.md and output_chunk*.md
    for pattern in ['chunk*.md', 'output_chunk*.md']:
        for filepath in glob.glob(os.path.join(temp_dir, pattern)):
            os.remove(filepath)
            removed.append(os.path.basename(filepath))

    # Remove specific intermediate files
    for name in ['input.html', 'input.md', 'output.html']:
        filepath = os.path.join(temp_dir, name)
        if os.path.exists(filepath):
            os.remove(filepath)
            removed.append(name)

    if removed:
        print(f"Removed {len(removed)} intermediate file(s):")
        # Summarize chunk files instead of listing each one
        chunk_files = [f for f in removed if 'chunk' in f]
        other_files = [f for f in removed if 'chunk' not in f]
        if chunk_files:
            print(f"  {len(chunk_files)} chunk files (chunk*.md, output_chunk*.md)")
        for f in other_files:
            print(f"  {f}")
    else:
        print("No intermediate files to remove.")


if __name__ == "__main__":
    main()
