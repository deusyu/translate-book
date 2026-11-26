#!/usr/bin/env python3
"""
convert.py - Convert PDF/DOCX/EPUB to Markdown chunks via Calibre HTMLZ
Combines the original steps 1-2 into a single script.

Markdown input (.md/.markdown) skips Calibre entirely. Use it for PDFs that
Calibre's coordinate-heuristic reflow mangles (formulas, tables, multi-column
layouts): extract them with a layout-aware parser such as MinerU, Marker or
docling first, then pass the resulting Markdown here.
"""

import os
import sys
import subprocess
import zipfile
import shutil
import tempfile
import argparse
import base64
import binascii
import bisect
import glob
import hashlib
import json
import re
import stat
import unicodedata
import urllib.parse
from collections import namedtuple

from manifest import chunk_file_name, create_manifest, file_hash, list_chunk_files, nth_chunk_id

if os.name == "nt":
    sys.exit("translate-book runs on macOS and Linux only. "
             "On Windows, run it in WSL (Windows Subsystem for Linux).")


def find_calibre_convert():
    """Find ebook-convert command from Calibre installation"""
    possible_paths = [
        "/Applications/calibre.app/Contents/MacOS/ebook-convert",
        "/usr/bin/ebook-convert",
        "/usr/local/bin/ebook-convert",
        "ebook-convert"  # If in PATH
    ]

    for path in possible_paths:
        try:
            result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                print(f"Found Calibre ebook-convert: {path}")
                return path
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    return None


def convert_to_htmlz(input_file, htmlz_file, calibre_path):
    """Convert input file to HTMLZ using Calibre"""
    try:
        print(f"Converting {input_file} to HTMLZ...")
        cmd = [calibre_path, input_file, htmlz_file]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if result.returncode == 0:
            file_size = os.path.getsize(htmlz_file)
            print(f"HTMLZ conversion successful: {htmlz_file} ({file_size} bytes)")
            return True
        else:
            print(f"HTMLZ conversion failed: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        print("HTMLZ conversion timed out")
        return False
    except Exception as e:
        print(f"HTMLZ conversion error: {e}")
        return False


def extract_metadata_from_htmlz(extract_dir):
    """Extract metadata from metadata.opf file in HTMLZ"""
    try:
        import xml.etree.ElementTree as ET

        metadata_file = None
        for root, dirs, files in os.walk(extract_dir):
            for file in files:
                if file.lower() == 'metadata.opf':
                    metadata_file = os.path.join(root, file)
                    break
            if metadata_file:
                break

        if not metadata_file:
            return {}

        tree = ET.parse(metadata_file)
        root = tree.getroot()

        namespaces = {
            'opf': 'http://www.idpf.org/2007/opf',
            'dc': 'http://purl.org/dc/elements/1.1/',
            'dcterms': 'http://purl.org/dc/terms/'
        }

        metadata = {}

        title_elem = root.find('.//dc:title', namespaces)
        if title_elem is not None and title_elem.text:
            metadata['title'] = title_elem.text.strip()

        creator_elem = root.find('.//dc:creator', namespaces)
        if creator_elem is not None and creator_elem.text:
            metadata['creator'] = creator_elem.text.strip()

        publisher_elem = root.find('.//dc:publisher', namespaces)
        if publisher_elem is not None and publisher_elem.text:
            metadata['publisher'] = publisher_elem.text.strip()

        language_elem = root.find('.//dc:language', namespaces)
        if language_elem is not None and language_elem.text:
            metadata['language'] = language_elem.text.strip()

        return metadata

    except Exception as e:
        print(f"Warning: Error extracting metadata: {e}")
        return {}


def _html_title(html_file):
    """The <title> of an HTML file, or None. Calibre copies the book title there."""
    try:
        with open(html_file, 'r', encoding='utf-8', errors='replace') as f:
            head = f.read(65536)
    except OSError:
        return None
    match = re.search(r'<title[^>]*>(.*?)</title>', head, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    import html as html_lib
    title = html_lib.unescape(re.sub(r'\s+', ' ', match.group(1))).strip()
    return title or None


def extract_htmlz(htmlz_file, temp_dir):
    """Extract HTMLZ file and return paths to HTML and images"""
    try:
        with zipfile.ZipFile(htmlz_file, 'r') as zip_file:
            zip_file.extractall(temp_dir)

        html_file = None
        images_dir = None

        for root, dirs, files in os.walk(temp_dir):
            for file in files:
                if file.lower() in ['index.html', 'index.htm']:
                    html_file = os.path.join(root, file)
                    break
            for dir_name in dirs:
                if dir_name.lower() in ['images', 'image', 'pics', 'pictures']:
                    images_dir = os.path.join(root, dir_name)
                    break

        if not html_file:
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    if file.lower().endswith(('.html', '.htm')):
                        html_file = os.path.join(root, file)
                        break
                if html_file:
                    break

        return html_file, images_dir

    except Exception as e:
        print(f"Error extracting HTMLZ: {e}")
        return None, None


# The temp dir path appears in every shell command of SKILL.md, so it may only
# hold ASCII letters, digits and `._-/` plus non-ASCII letters, digits and
# combining marks: none of them is shell or glob syntax in sh, bash or zsh, and
# non-ASCII characters are bytes >= 0x80, which no shell treats specially.
TEMP_STEM_MAX_BYTES = 160


def _is_variation_selector(ch):
    cp = ord(ch)
    return 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF or 0x180B <= cp <= 0x180F


def safe_temp_stem(name):
    """Turn a file name stem into the stem of a shell-safe temp dir name.

    After NFKC (full-width punctuation folds to ASCII and is then replaced),
    keeps ASCII letters, digits and `._-`, non-ASCII letters and digits, and a
    combining mark only right after a kept letter, digit or mark. Enclosing
    marks and variation selectors are dropped; every other character becomes
    `_`. Runs of `_` collapse, `_.-` go from both ends, and the result is cut
    at a character boundary to TEMP_STEM_MAX_BYTES of UTF-8. 'book' if empty.
    """
    out = []
    after_word = False
    for ch in unicodedata.normalize('NFKC', name):
        if ch.isascii():
            keep = ch.isalnum() or ch in '._-'
            out.append(ch if keep else '_')
            after_word = ch.isalnum()
            continue
        category = unicodedata.category(ch)
        if category == 'Me' or _is_variation_selector(ch):
            continue
        if category[0] in 'LN' or (category in ('Mn', 'Mc') and after_word):
            out.append(ch)
            after_word = True
        else:
            out.append('_')
            after_word = False
    stem = re.sub(r'_+', '_', ''.join(out)).strip('_.-')
    size = 0
    for i, ch in enumerate(stem):
        size += len(ch.encode('utf-8'))
        if size > TEMP_STEM_MAX_BYTES:
            stem = stem[:i].rstrip('_.-')
            break
    return stem or 'book'


def build_temp_dir(input_file, temp_root=None):
    """Return the working directory path for an input file: <temp_root>/<safe stem>_temp.

    Pure: looks at no file. Without temp_root the path is relative to the cwd.
    """
    stem = os.path.splitext(os.path.basename(input_file))[0]
    leaf = f"{safe_temp_stem(stem)}_temp"
    if temp_root:
        return os.path.join(temp_root, leaf)
    return leaf


def legacy_temp_dir(input_file, temp_root=None):
    """The temp dir path versions up to 1.x used: the raw file name stem plus _temp."""
    leaf = os.path.splitext(os.path.basename(input_file))[0] + "_temp"
    return os.path.join(temp_root, leaf) if temp_root else leaf


def _is_safe_path_char(ch):
    if ch.isascii():
        return ch.isalnum() or ch in '._-'
    category = unicodedata.category(ch)
    return (category[0] in 'LN' or category in ('Mn', 'Mc')) and not _is_variation_selector(ch)


def validate_temp_root(temp_root):
    """Return temp_root with `~` expanded and trailing slashes removed.

    Every `/`-separated segment must consist of the characters a temp dir
    name may hold, must not be `.` or `..`, and must not start with `-`, so
    the path is safe in a shell command and is never read as an option. The
    path is checked, not rewritten. Raises ValueError naming the first bad
    segment; an existing path must be a directory.
    """
    root = os.path.expanduser(temp_root)
    if '\\' in root:
        raise ValueError(f"temp_root {temp_root!r} contains a backslash; use / between directories")
    body = root[1:] if root.startswith('/') else root
    body = body.rstrip('/')
    if not body:
        raise ValueError(f"temp_root {temp_root!r} names no directory")
    for segment in body.split('/'):
        problem = None
        if not segment:
            problem = "is empty (two slashes in a row)"
        elif segment in ('.', '..'):
            problem = "is . or .., which is not allowed"
        elif segment.startswith('-'):
            problem = "starts with -, which a command would read as an option"
        elif unicodedata.category(segment[0]).startswith('M'):
            problem = "starts with a combining mark, which belongs after a letter or digit"
        elif not all(_is_safe_path_char(ch) for ch in segment):
            problem = ("has characters other than letters, digits, '.', '_' and '-', "
                       "such as a space or punctuation")
        if problem:
            raise ValueError(
                f"temp_root {temp_root!r}: the part {segment!r} {problem}. The temp directory "
                f"path goes into shell commands, so choose a directory whose path has only "
                f"letters, digits, '.', '_', '-' and '/', or leave temp_root out to use the "
                f"current directory."
            )
    root = ('/' if root.startswith('/') else '') + body
    if os.path.exists(root) and not os.path.isdir(root):
        raise ValueError(f"temp_root {root!r} exists but is not a directory")
    return root


SOURCE_FINGERPRINT_FILE = "source_fingerprint.json"

# Files whose presence means the temp dir carries conversion state derived
# from some source book — and must therefore be tied to the current one.
_SOURCE_CACHE_MARKERS = (
    "input.html",
    "input.md",
    "manifest.json",
    "run_state.json",
    "glossary.json",
    "output.md",
)


class TempDirError(Exception):
    """The temp dir cannot be used for this run; the message says why and what to do."""


class UnreadableFingerprint(Exception):
    """source_fingerprint.json is there but is not a record translate-book wrote."""


class SourceChanged(Exception):
    """The book file's bytes differ from the ones this run identified."""


def source_fingerprint(input_file):
    """Stable identity of the exact source bytes being converted."""
    return {
        "path": os.path.realpath(input_file),
        "size": os.path.getsize(input_file),
        "sha256": file_hash(input_file),
    }


_FINGERPRINT_VERSION = 2
_FINGERPRINT_KEYS = {"version", "path", "size", "sha256", "parser", "target_lang"}
# What versions before 2 wrote; they converted without a parser and kept the
# target language in config.txt only.
_OLDER_FINGERPRINT_KEYS = {"path", "size", "sha256"}


def run_identity(input_file, target_lang, parser=None):
    """What a temp dir's chunks and translations are derived from.

    The source bytes, the parser, which shapes the Markdown, and the target
    language of the translations. Stored as source_fingerprint.json.
    """
    identity = {"version": _FINGERPRINT_VERSION}
    identity.update(source_fingerprint(input_file))
    identity.update(parser=parser, target_lang=target_lang)
    return identity


class Own:
    """A file or directory this run wrote, created or read, held open until close().

    While it is open its inode cannot pass to another file, so a file found
    later with that inode is this one. data is the bytes this run wrote or
    read; None for a directory.
    """

    def __init__(self, fd, data=None):
        self._fd, self.data = fd, data

    def fileno(self):
        return self._fd

    def is_this(self, found):
        """Whether the os.stat_result found is of this file or directory."""
        return self._fd is not None and os.path.samestat(found, os.fstat(self._fd))

    def unchanged(self):
        """Whether the file still holds data, read through the descriptor."""
        return self._fd is not None and os.pread(self._fd, len(self.data) + 1, 0) == self.data

    def close(self):
        fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)

    __del__ = close


def _fingerprint_bytes(fingerprint):
    return (json.dumps(fingerprint, indent=2, sort_keys=True) + '\n').encode('utf-8')


def _write_source_fingerprint(temp_dir, fingerprint):
    """Write the fingerprint; return the file written as an Own."""
    os.makedirs(temp_dir, exist_ok=True)
    data = _fingerprint_bytes(fingerprint)
    fd, tmp = tempfile.mkstemp(dir=temp_dir, prefix='.fingerprint.', suffix='.tmp')
    written = Own(fd, data)
    try:
        with open(fd, 'wb', closefd=False) as f:
            f.write(data)
        os.replace(tmp, os.path.join(temp_dir, SOURCE_FINGERPRINT_FILE))
    except BaseException:
        _remove_own_file(tmp, written, same_bytes=False)
        written.close()
        raise
    return written


def _claim_temp_dir(temp_dir, identity):
    """Write the identity into a temp dir that has none.

    Returns the file written as an Own, or None if a fingerprint appeared meanwhile."""
    path = os.path.join(temp_dir, SOURCE_FINGERPRINT_FILE)
    data = _fingerprint_bytes(identity)
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o644)
    except FileExistsError:
        return None
    try:
        with open(fd, 'wb', closefd=False) as f:
            f.write(data)
    except BaseException:
        os.close(fd)
        raise
    return Own(fd, data)


def _make_temp_dir(temp_dir):
    """Create temp_dir; return it as an Own if this call created it, None if it was there."""
    os.makedirs(os.path.dirname(temp_dir) or '.', exist_ok=True)
    try:
        os.mkdir(temp_dir)
    except FileExistsError:
        return None
    return Own(os.open(temp_dir, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)))


def _aside(path):
    """A name in path's directory that no one else uses, to move path to before checking it."""
    return os.path.join(os.path.dirname(path) or '.', f".translate-book-{os.urandom(8).hex()}.removing")


def _put_back(aside, path):
    """Move aside back to path, never over something that appeared at path meanwhile."""
    try:
        if os.path.isdir(aside) and not os.path.islink(aside):
            if os.path.lexists(path):
                raise FileExistsError(path)
            os.rename(aside, path)
        else:
            os.link(aside, path, follow_symlinks=False)
            os.unlink(aside)
    except OSError:
        print(f"Warning: {path} changed while this run was checking it. What was there is "
              f"now {aside}; if it is yours, rename it back.")


def _remove_own_file(path, own, same_bytes=True):
    """Remove path if it is still the file own holds; return whether it was.

    With same_bytes, the file must also still hold own's bytes. Nothing at
    path is opened, so a FIFO or device put there neither blocks nor changes:
    the file is compared by inode, and its bytes are read through own's
    descriptor. path is moved to a name of this run's own in the same
    directory before the last check, so that the file checked is the file
    removed; anything else is moved back.
    """
    try:
        if not own.is_this(os.lstat(path)):
            return False
    except OSError:
        return False
    aside = _aside(path)
    try:
        os.rename(path, aside)
    except OSError:
        return False
    try:
        found = os.lstat(aside)
        is_own = stat.S_ISREG(found.st_mode) and own.is_this(found) and (not same_bytes or own.unchanged())
    except OSError:
        is_own = False
    if is_own:
        os.unlink(aside)
        return True
    _put_back(aside, path)
    return False


def _remove_own_dir(path, own):
    """Remove path if it is still the directory own holds and is empty; return whether it was."""
    try:
        if not own.is_this(os.lstat(path)):
            return False
    except OSError:
        return False
    aside = _aside(path)
    try:
        os.rename(path, aside)
    except OSError:
        return False
    try:
        found = os.lstat(aside)
        if stat.S_ISDIR(found.st_mode) and own.is_this(found):
            os.rmdir(aside)
            return True
    except OSError:
        pass
    _put_back(aside, path)
    return False


def _empty_own_dir(fd):
    """Remove everything inside the directory open as fd, through fd.

    Only what is inside that directory goes, whatever path leads to it now."""
    for name in os.listdir(fd):
        try:
            if stat.S_ISDIR(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode):
                shutil.rmtree(name, dir_fd=fd, ignore_errors=True)
            else:
                os.unlink(name, dir_fd=fd)
        except OSError:
            pass


class OwnDir:
    """A directory this run created for files of its own, held open from the start.

    remove() empties it through that descriptor and removes it only if path
    still leads to it, so a directory put at path meanwhile is left as it is.
    """

    def __init__(self, path):
        changed = OSError(f"{path} changed right after this run created it; it was left as it is")
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0))
        except OSError:
            raise changed from None
        self.path, self._own = path, Own(fd)
        try:
            if os.listdir(fd):
                raise changed
        except BaseException:
            self._own.close()
            raise

    @classmethod
    def make(cls, path):
        """Create the directory path; raises FileExistsError if anything is there."""
        os.mkdir(path)
        return cls(path)

    @classmethod
    def make_temp(cls, prefix):
        """Create a new directory in tempfile's directory."""
        return cls(tempfile.mkdtemp(prefix=prefix))

    def fileno(self):
        return self._own.fileno()

    def make_dir(self, name):
        """Create the directory name inside this one; return its path."""
        os.mkdir(name, dir_fd=self.fileno())
        return os.path.join(self.path, name)

    def remove(self):
        if self._own.fileno() is None:
            return
        try:
            _empty_own_dir(self._own.fileno())
            if not _remove_own_dir(self.path, self._own) and os.path.lexists(self.path):
                print(f"Warning: {self.path} was left as it is: it is no longer the empty directory "
                      f"this run created. Remove it once you have checked what is in it.")
        finally:
            self._own.close()


def _fingerprint_problem(data):
    """Why data is not a fingerprint translate-book wrote, or None.

    Every field must be there with its type, so a partial record never passes
    for a complete one."""
    if not isinstance(data, dict):
        return "it does not hold a JSON object"
    if "version" in data:
        if type(data["version"]) is not int or data["version"] != _FINGERPRINT_VERSION:
            return f"its version {data['version']!r} is not {_FINGERPRINT_VERSION}"
        expected = _FINGERPRINT_KEYS
    else:
        expected = _OLDER_FINGERPRINT_KEYS
    if set(data) != expected:
        return f"its keys are {', '.join(sorted(data)) or 'none'}, not {', '.join(sorted(expected))}"
    if not isinstance(data["path"], str) or not data["path"]:
        return "its path is not a file path"
    if type(data["size"]) is not int or data["size"] < 0:
        return "its size is not a number of bytes"
    if not isinstance(data["sha256"], str) or not re.fullmatch(r'[0-9a-f]{64}', data["sha256"]):
        return "its sha256 is not 64 lowercase hex digits"
    if expected is _OLDER_FINGERPRINT_KEYS:
        return None
    if data["parser"] is not None and not (isinstance(data["parser"], str) and data["parser"] in PARSERS):
        return f"its parser {data['parser']!r} is not one of {', '.join(PARSERS)} or null"
    try:
        if normalize_target_lang(data["target_lang"]) != data["target_lang"]:
            raise ValueError
    except ValueError:
        return f"its target_lang {data['target_lang']!r} is not a language code"
    return None


def _load_source_fingerprint(temp_dir):
    """The identity recorded in temp_dir, or None when it records none.

    Raises UnreadableFingerprint when the file is there but cannot be read or
    is not a record translate-book wrote: a damaged record must not pass for
    the missing one of a temp dir made before fingerprints existed.
    """
    path = os.path.join(temp_dir, SOURCE_FINGERPRINT_FILE)
    if not os.path.lexists(path):
        return None
    if os.path.islink(path):
        raise UnreadableFingerprint("it is a symlink")
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise UnreadableFingerprint(str(e)) from None
    problem = _fingerprint_problem(data)
    if problem:
        raise UnreadableFingerprint(problem)
    return data


def _has_reusable_source_cache(temp_dir):
    if not os.path.isdir(temp_dir):
        return False
    for name in _SOURCE_CACHE_MARKERS:
        if os.path.exists(os.path.join(temp_dir, name)):
            return True
    for pattern in ('chunk*.md', 'output_chunk*.md'):
        if glob.glob(os.path.join(glob.escape(temp_dir), pattern)):
            return True
    return False


def _config_values(temp_dir):
    """config.txt as a dict; empty when it is missing or unreadable."""
    values = {}
    try:
        with open(os.path.join(temp_dir, "config.txt"), 'r', encoding='utf-8') as f:
            for line in f:
                line = line.rstrip('\r\n')
                if '=' in line and not line.lstrip().startswith('#'):
                    key, value = line.split('=', 1)
                    values[key.strip()] = value
    except (OSError, UnicodeDecodeError):
        pass
    return values


def _config_metadata(temp_dir):
    """The book metadata an earlier run recorded in config.txt, keyed as create_config_file takes it."""
    stored = _config_values(temp_dir)
    return {key: stored[name] for key, name in (
        ('title', 'original_title'), ('creator', 'creator'),
        ('publisher', 'publisher'), ('language', 'source_language'),
    ) if name in stored}


def _stored_target_lang(temp_dir, stored):
    """The target language a temp dir was converted for, or None if unrecorded.

    A version 2 fingerprint records it; for an older one, or none, only a
    valid output_lang in config.txt does."""
    if "version" in stored:
        return stored["target_lang"]
    try:
        return normalize_target_lang(_config_values(temp_dir).get("output_lang", ""))
    except ValueError:
        return None


def _language_mismatch(temp_dir, stored_lang, target_lang):
    return 'mismatch', (
        f"{temp_dir}/ holds a translation into {stored_lang}; this run asks for "
        f"{target_lang}. Mixing them would give a book in two languages."
    )


def _check_unfingerprinted_cache(temp_dir, identity, input_file):
    """check_source_cache for a cache made by a version without fingerprints."""
    recorded = _config_values(temp_dir).get("input_file", "")
    name = os.path.basename(input_file) if input_file else None
    if not (name and recorded and os.path.basename(recorded) == _config_value(name)):
        return 'unknown', (
            f"{temp_dir}/ holds a conversion by an older version, without "
            f"{SOURCE_FINGERPRINT_FILE}, of "
            + (f"a file called {os.path.basename(recorded)!r}" if recorded else "an unrecorded file")
            + ", so it cannot be tied to this book."
        )
    if identity["parser"]:
        return 'mismatch', (
            f"{temp_dir}/ holds a conversion by an older version, made without a parser; this "
            f"run asks for {identity['parser']!r}, which would give different chunks."
        )
    stored_lang = _stored_target_lang(temp_dir, {})
    if stored_lang is None:
        return 'unknown', (
            f"{temp_dir}/ holds a conversion by an older version, without "
            f"{SOURCE_FINGERPRINT_FILE}, and its config.txt records no usable target "
            f"language, so it cannot be tied to this run."
        )
    if stored_lang != identity["target_lang"]:
        return _language_mismatch(temp_dir, stored_lang, identity["target_lang"])
    return 'adopt', (
        f"{temp_dir}/ holds a conversion by an older version, without "
        f"{SOURCE_FINGERPRINT_FILE}. Its config.txt names a file called {name!r} and the "
        f"target language {stored_lang}, so it is taken to be this book's and fingerprinted "
        f"now. If that file was replaced since, set temp_root to another directory and "
        f"convert again."
    )


def check_source_cache(temp_dir, identity, input_file=None):
    """Compare the temp dir's recorded identity with the current run.

    Returns (status, message):
      (None, None)          — absent or empty temp dir, or the identity matches.
      ('adopt', message)    — cache from a version without fingerprints whose
                              config.txt names a file of the same name as
                              input_file and the same target language, made
                              without a parser: take it as this book's (keeps old
                              temp dirs resumable) and record the identity now.
      ('restart', None)     — this book's temp dir, recorded with other settings
                              by a run that converted nothing: record this run's.
      ('mismatch', message) — built from other source bytes, with another
                              parser, or for another target language.
      ('unknown', message)  — files that cannot be tied to this book: an
                              unreadable fingerprint, a cache without fingerprint
                              for another or unrecorded file name or language,
                              or files translate-book did not write.

    Source content is compared by sha256 and size, so moving or renaming the
    source file does not invalidate the cache.
    """
    if not os.path.isdir(temp_dir):
        return None, None
    try:
        stored = _load_source_fingerprint(temp_dir)
    except UnreadableFingerprint as e:
        return 'unknown', (
            f"{temp_dir}/{SOURCE_FINGERPRINT_FILE} cannot be read ({e}), so it is not known "
            f"which book and settings {temp_dir}/ holds."
        )
    if stored is None:
        if not _has_reusable_source_cache(temp_dir):
            if os.listdir(temp_dir):
                return 'unknown', (
                    f"{temp_dir}/ is not empty and holds no translate-book conversion, "
                    f"so it is not used.")
            return None, None
        return _check_unfingerprinted_cache(temp_dir, identity, input_file)

    if stored["sha256"] != identity["sha256"] or stored["size"] != identity["size"]:
        return 'mismatch', (
            f"{temp_dir}/ was created from different source bytes: {stored.get('path')!r} "
            f"(sha256 {str(stored.get('sha256', ''))[:12]}...), while this run converts "
            f"{identity['path']!r} (sha256 {identity['sha256'][:12]}...). Reusing its chunks "
            f"would translate the wrong book. Two books can get the same temp directory name "
            f"because the name keeps only letters, digits, '.', '_' and '-' and is shortened."
        )
    if not _has_reusable_source_cache(temp_dir):
        return (None, None) if stored == identity else ('restart', None)
    stored_parser = stored.get("parser")
    if stored_parser != identity["parser"]:
        return 'mismatch', (
            f"{temp_dir}/ was converted with "
            + (f"the parser {stored_parser!r}" if stored_parser else "no parser")
            + "; this run asks for "
            + (f"the parser {identity['parser']!r}" if identity['parser'] else "no parser")
            + ", which would give different chunks."
        )
    stored_lang = _stored_target_lang(temp_dir, stored)
    if stored_lang is None:
        return 'unknown', (
            f"{temp_dir}/{SOURCE_FINGERPRINT_FILE} was written by an older version, which "
            f"did not record the target language, and config.txt records no usable one, so "
            f"it is not known which language {temp_dir}/ holds."
        )
    if stored_lang != identity["target_lang"]:
        return _language_mismatch(temp_dir, stored_lang, identity["target_lang"])
    return None, None


def _refuse(status, message, temp_dir):
    if status == 'unknown':
        advice = "Set temp_root to another directory to convert this book there."
    else:
        advice = (f"To convert this book, set temp_root to another directory. To continue the "
                  f"run in {temp_dir}/, use the source file, parser and target language it "
                  f"was made from.")
    raise TempDirError(f"{message}\nNothing was changed. {advice}")


def _migrate_legacy_temp_dir(legacy, temp_dir, identity, input_file):
    """Rename this book's temp dir from its 1.x name to temp_dir, when that is safe.

    Renames only a real directory (never a symlink) that verifiably holds this
    book's conversion, and only when temp_dir does not exist. Returns the notes
    to print; raises TempDirError when this book's cache and temp_dir conflict.
    """
    if os.path.islink(legacy):
        raise TempDirError(
            f"{legacy} is a symlink, and translate-book never renames a symlink to the new "
            f"temp directory name {temp_dir}. If it leads to this book's earlier run, move "
            f"that directory to {temp_dir} yourself; otherwise rename or move the symlink. "
            f"Then run again, or set temp_root to another directory."
        )
    if not os.path.isdir(legacy):
        return []
    if os.path.lexists(temp_dir) and os.path.isdir(temp_dir) and os.path.samefile(legacy, temp_dir):
        return []  # the same directory under another spelling (case or Unicode form)

    status, message = check_source_cache(legacy, identity, input_file)
    if status is None and not _has_reusable_source_cache(legacy) \
            and _load_source_fingerprint(legacy) is None:
        return []  # nothing to resume there
    if status in ('mismatch', 'unknown'):
        return [f"Note: {legacy} was left as it is: {message}"]

    if not os.path.lexists(temp_dir):
        try:
            os.rename(legacy, temp_dir)
        except OSError as e:
            raise TempDirError(
                f"Could not rename this book's temp directory {legacy} to {temp_dir}: {e}. "
                f"Nothing was changed; rename it yourself, then run again."
            ) from None
        return [f"Renamed {legacy} to {temp_dir}: temp directory names now keep only "
                f"letters, digits, '.', '_' and '-'."]

    target_status, _ = check_source_cache(temp_dir, identity, input_file)
    if target_status is None and _load_source_fingerprint(temp_dir) is not None:
        return [f"Note: {legacy} was left as it is; using {temp_dir}, which holds this "
                f"book's conversion too."]
    raise TempDirError(
        f"This book's earlier run is in {legacy}, which now has to be named {temp_dir}, "
        f"but {temp_dir} already exists and is not known to hold this book's conversion. "
        f"Nothing was changed. Rename or move {temp_dir} to free the name, then run again, "
        f"or set temp_root to another directory."
    )


class TempDirClaim(namedtuple("TempDirClaim", "directory fingerprint")):
    """What prepare_temp_dir() made, so that release_temp_dir() removes that only.

    directory is the temp dir as an Own if this run's os.mkdir created it;
    fingerprint is the fingerprint file as an Own if this run wrote it. Each
    is None otherwise: a run that joins a temp dir with this run's identity
    already in it owns neither.
    """
    __slots__ = ()

    def close(self):
        for own in self:
            if own is not None:
                own.close()


def prepare_temp_dir(input_file, temp_root, identity):
    """Return the temp dir for this run, after migrating, checking or claiming it.

    Raises TempDirError, without changing anything, when the temp dir is a
    symlink, belongs to another book or run, has an unreadable fingerprint, or
    holds files of unknown origin. A fresh temp dir gets the identity at once,
    so a second book whose name maps to the same directory is refused even
    while the first is converting; release_temp_dir() gives it up again when
    the run converts nothing. Returns (temp_dir, notes, TempDirClaim).
    """
    temp_dir = build_temp_dir(input_file, temp_root)
    notes = []
    if os.path.islink(temp_dir):
        raise TempDirError(f"{temp_dir} is a symlink; translate-book only works in a real "
                           f"directory. Set temp_root to another directory.")
    if os.path.lexists(temp_dir) and not os.path.isdir(temp_dir):
        raise TempDirError(f"{temp_dir} exists and is not a directory. Set temp_root to "
                           f"another directory.")
    legacy = legacy_temp_dir(input_file, temp_root)
    if legacy != temp_dir and os.path.lexists(legacy):
        notes += _migrate_legacy_temp_dir(legacy, temp_dir, identity, input_file)

    directory = None
    for _ in range(2):
        status, message = check_source_cache(temp_dir, identity, input_file)
        if status in ('mismatch', 'unknown'):
            _refuse(status, message, temp_dir)
        if status in ('adopt', 'restart'):
            if status == 'adopt':
                notes.append(f"Warning: {message}")
            return temp_dir, notes, TempDirClaim(directory, _write_source_fingerprint(temp_dir, identity))
        if _load_source_fingerprint(temp_dir) is not None:
            return temp_dir, notes, TempDirClaim(directory, None)
        directory = directory or _make_temp_dir(temp_dir)
        fingerprint = _claim_temp_dir(temp_dir, identity)
        if fingerprint is not None:
            return temp_dir, notes, TempDirClaim(directory, fingerprint)
    raise TempDirError(f"Could not claim {temp_dir}; another conversion may be using it.")


def release_temp_dir(temp_dir, claim):
    """After a failed run, remove what prepare_temp_dir() made if temp_dir holds nothing else.

    The fingerprint goes only if this run wrote it (claim.fingerprint), it is
    still that file, and temp_dir holds nothing besides it, such as conversion
    state a re-run of the same book can resume; with nothing else there, it
    would only keep other settings or another book out. The temp dir goes too
    if this run created it (claim.directory) and it is then empty. Each goes
    only if it is still what this run made: it is checked where it is, moved
    to a name of this run's own, checked again and removed, or moved back.
    """
    if claim.fingerprint is None:
        return
    try:
        if os.listdir(temp_dir) != [SOURCE_FINGERPRINT_FILE]:
            return
    except OSError:
        return
    if _remove_own_file(os.path.join(temp_dir, SOURCE_FINGERPRINT_FILE), claim.fingerprint) \
            and claim.directory is not None:
        try:
            if os.listdir(temp_dir):
                return
        except OSError:
            return
        _remove_own_dir(temp_dir, claim.directory)


def copy_source(input_file, scratch, identity):
    """Copy the book into scratch and return the copy's absolute path.

    scratch is the OwnDir this run created for the copy, outside the temp dir.
    The copy is hashed as it is written, and conversion reads only the copy,
    so the chunks come from the bytes the identity names even if the file is
    replaced meanwhile. Raises SourceChanged when the bytes differ.
    """
    name = os.path.basename(input_file)
    copy = os.path.abspath(os.path.join(scratch.path, name))
    digest, size = hashlib.sha256(), 0

    def in_scratch(path, flags):
        return os.open(path, flags, 0o600, dir_fd=scratch.fileno())

    with open(input_file, 'rb') as src, open(name, 'xb', opener=in_scratch) as dst:
        for block in iter(lambda: src.read(1 << 20), b''):
            digest.update(block)
            size += len(block)
            dst.write(block)
    if digest.hexdigest() != identity["sha256"] or size != identity["size"]:
        raise SourceChanged(
            f"{input_file} changed while it was being read (it now has {size} bytes, sha256 "
            f"{digest.hexdigest()[:12]}...). Nothing was converted; once the file stays the "
            f"same, run this step again."
        )
    return copy


def setup_temp_directory(temp_dir, html_file, images_dir):
    """Copy the HTML and images into the temp dir, keeping copies made before."""
    try:
        os.makedirs(temp_dir, exist_ok=True)

        input_html = os.path.join(temp_dir, "input.html")
        if os.path.exists(input_html):
            print(f"Skipping HTML copy - input.html already exists")
        else:
            shutil.copy2(html_file, input_html)
            print(f"Copied HTML to: {input_html}")

        if images_dir and os.path.exists(images_dir):
            target_images_dir = os.path.join(temp_dir, "images")
            if os.path.exists(target_images_dir):
                print(f"Skipping images copy - images directory already exists")
            else:
                shutil.copytree(images_dir, target_images_dir)
                print(f"Copied images to: {target_images_dir}")

        return temp_dir
    except Exception as e:
        print(f"Error setting up temp directory: {e}")
        return None


# =============================================================================
# Structural cleanup on pandoc's JSON AST (Calibre HTML -> Markdown)
# =============================================================================

_INLINE_TYPES = frozenset({
    "Str", "Emph", "Underline", "Strong", "Strikeout", "Superscript", "Subscript",
    "SmallCaps", "Quoted", "Cite", "Code", "Space", "SoftBreak", "LineBreak",
    "Math", "RawInline", "Link", "Image", "Note", "Span",
})
_SPACE_TYPES = frozenset({"Space", "SoftBreak", "LineBreak"})
_FORMATTING_TYPES = frozenset({
    "Emph", "Underline", "Strong", "Strikeout", "Superscript", "Subscript", "SmallCaps",
})
# Digits or note symbols, optionally bracketed and/or followed by a period:
# "1", "[12]", "(3)", "4.", "*", "†".
_FOOTNOTE_MARKER_RE = re.compile(r'^[\[(]?(\d{1,4}|[*†‡§¶‖]{1,3})[\])]?\.?$')
_NOTE_MARKER_TRAILERS = frozenset({".", ")", "]", ":"})


def _map_inline_lists(node, fn, top_down=False):
    """Rewrite every inline list of a pandoc JSON AST in place via fn(list) -> list."""
    if isinstance(node, dict):
        if isinstance(node.get("c"), list):
            _map_inline_lists(node["c"], fn, top_down)
        return
    if not isinstance(node, list):
        return
    is_inline_list = bool(node) and all(
        isinstance(x, dict) and x.get("t") in _INLINE_TYPES for x in node
    )
    if is_inline_list and top_down:
        node[:] = fn(node)
    for item in node:
        _map_inline_lists(item, fn, top_down)
    if is_inline_list and not top_down:
        node[:] = fn(node)


def _inline_text(inlines):
    parts = []
    for x in inlines:
        t = x.get("t")
        if t == "Str":
            parts.append(x["c"])
        elif t in _SPACE_TYPES:
            parts.append(" ")
        elif t in _FORMATTING_TYPES:
            parts.append(_inline_text(x["c"]))
        elif t in ("Span", "Link", "Quoted"):
            parts.append(_inline_text(x["c"][1]))
        elif t == "Code":
            parts.append(x["c"][1])
    return "".join(parts)


def _is_anchor(inline):
    """An element that renders nothing, e.g. an id-only <a id="page_12"/>."""
    if inline.get("t") not in ("Span", "Link"):
        return False
    return all(_is_anchor(x) for x in inline["c"][1])


def _is_filler(inline):
    """Whitespace, or a span/link holding nothing but whitespace and anchors."""
    if inline.get("t") in _SPACE_TYPES:
        return True
    if inline.get("t") not in ("Span", "Link"):
        return False
    return all(_is_filler(x) for x in inline["c"][1])


def _footnote_marker(inlines):
    match = _FOOTNOTE_MARKER_RE.match(_inline_text(inlines).strip())
    return match.group(1) if match else None


def _footnote_link(inline):
    """Return (link, anchor ids) when inline is a footnote-marker link to an in-book target.

    The link may sit alone or inside <sup>/<span> wrappers; the ids are every
    anchor a note's back-link may point at (the link's own id and its wrapping
    spans' ids).
    """
    t = inline.get("t")
    if t == "Link":
        attr, content, (url, _title) = inline["c"]
        if url.startswith("#") and len(url) > 1 and _footnote_marker(content):
            return inline, [attr[0]] if attr[0] else []
        return None
    if t in ("Superscript", "Span"):
        content = inline["c"] if t == "Superscript" else inline["c"][1]
        solid = [x for x in content if not _is_filler(x)]
        if len(solid) == 1:
            found = _footnote_link(solid[0])
            if found and t == "Span" and inline["c"][0][0]:
                found[1].append(inline["c"][0][0])
            return found
    return None


def _index_footnote_refs(blocks):
    """Map anchor id -> footnote reference link, for marker links inside running text.

    A marker link that opens its line is a note's back-link or a contents
    entry, never a reference, so it is not indexed.
    """
    refs = {}

    def visit(inlines):
        after_text = False
        for x in inlines:
            if after_text:
                found = _footnote_link(x)
                if found:
                    link, anchor_ids = found
                    for anchor_id in anchor_ids:
                        refs.setdefault(anchor_id, link)
            if not _is_filler(x):
                after_text = True
        return inlines

    _map_inline_lists(blocks, visit)
    return refs


def _note_body(block, refs):
    """Return (reference link, note inlines) when block opens with a back-link to a known reference."""
    if block.get("t") not in ("Para", "Plain"):
        return None
    inlines = block["c"]
    i = 0
    while i < len(inlines) and _is_filler(inlines[i]):
        i += 1
    found = _footnote_link(inlines[i]) if i < len(inlines) else None
    if not found:
        return None
    backlink = found[0]
    ref = refs.get(backlink["c"][2][0][1:])
    if ref is None or _footnote_marker(ref["c"][1]) != _footnote_marker(backlink["c"][1]):
        return None
    body = inlines[i + 1:]
    while body and (
        _is_filler(body[0])
        or (body[0].get("t") == "Str" and body[0]["c"] in _NOTE_MARKER_TRAILERS)
    ):
        body = body[1:]
    return (ref, body) if body else None


def _collect_notes(blocks, refs, found):
    """Append (ref, container, block, note blocks) for note paragraphs in body/div block lists."""
    for block in blocks:
        if block.get("t") == "Div":
            attr, inner = block["c"]
            solid = [b for b in inner if not (
                b.get("t") in ("Para", "Plain") and all(_is_filler(x) for x in b["c"])
            )]
            hit = _note_body(solid[0], refs) if solid else None
            # A div is the whole note only when the reference points at the div itself.
            if hit and attr[0] and hit[0]["c"][2][0] == "#" + attr[0]:
                found.append((hit[0], blocks, block, [{"t": "Para", "c": hit[1]}] + solid[1:]))
                continue
            _collect_notes(inner, refs, found)
            continue
        hit = _note_body(block, refs)
        if hit:
            found.append((hit[0], blocks, block, [{"t": "Para", "c": hit[1]}]))


def _pandoc_notes(node):
    """Return the block lists of the Note elements found anywhere in node."""
    found = []
    if isinstance(node, dict):
        if node.get("t") == "Note":
            found.append(node["c"])
        if isinstance(node.get("c"), list):
            found.extend(_pandoc_notes(node["c"]))
    elif isinstance(node, list):
        for item in node:
            found.extend(_pandoc_notes(item))
    return found


def convert_footnotes(blocks):
    """Turn EPUB-style footnotes into pandoc notes. Returns the number converted.

    A reference is a short marker link in running text (`<a href="#n1"><sup>1</sup></a>`,
    `<sup><a>1</a></sup>`, `[1]`); its note is the paragraph (or the div the
    reference points at) that opens with a back-link to that reference with the
    same marker. Calibre renames ids and classes, so pairing relies only on this
    two-way link structure. Pandoc's Markdown writer then numbers the notes
    across the whole book, so labels stay unique when per-chapter numbering
    restarts.
    """
    refs = _index_footnote_refs(blocks)
    if not refs:
        return 0
    candidates = []
    _collect_notes(blocks, refs, candidates)

    notes = {}
    for ref, container, block, content in candidates:
        notes.setdefault(id(ref), (container, block, content))
    # Pandoc before 3.13 drops a note written inside another note, so nothing
    # that would end up nested is converted: a reference inside a note (one
    # pandoc already read, e.g. a DPUB-ARIA note, or one converted here) stays
    # inline, and a note whose text holds a pandoc note stays a paragraph.
    nested = set()
    for note in _pandoc_notes(blocks) + [[block] for _container, block, _content in notes.values()]:
        nested.update(id(link) for link in _index_footnote_refs(note).values())
    for key, (_container, _block, content) in notes.items():
        if _pandoc_notes(content):
            nested.add(key)
    for key in nested:
        notes.pop(key, None)

    used = set()

    def replace(inlines):
        out = []
        for x in inlines:
            found = _footnote_link(x)
            key = id(found[0]) if found else None
            if key in notes and key not in used:
                used.add(key)
                out.append({"t": "Note", "c": notes[key][2]})
            else:
                out.append(x)
        return out

    _map_inline_lists(blocks, replace, top_down=True)

    for key in used:
        container, block, _content = notes[key]
        for i, candidate in enumerate(container):
            if candidate is block:
                del container[i]
                break
    return len(used)


_URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _is_in_book_target(target):
    """True for a link into the source book rather than out of it.

    Calibre rewrites resolvable in-book links to `#anchor` targets, but a link
    to a part of the book it did not keep (a cover page, a nav file) stays a
    relative file link such as `9780063517578_Cover.xhtml#cover`. Neither kind
    can resolve in the translated book, so both lose their target.
    """
    return target.startswith("#") or not _URL_SCHEME.match(target)


def _unwrap_inline_markup(inlines):
    """Unwrap spans and in-book links, clear the attributes of other links, and
    drop empty anchors inside text.

    Lists made only of anchors are left alone: they become `[]` lines that
    clean_calibre_markers() drops after its page-number pass, which keeps that
    pass seeing the same neighbouring lines as before.
    """
    if all(_is_filler(x) for x in inlines):
        return inlines
    out = []
    # Unwrapped children are processed in turn, so an anchor nested in a span
    # is still dropped while the span's spaces and line breaks are kept.
    pending = list(reversed(inlines))
    while pending:
        x = pending.pop()
        t = x.get("t")
        if _is_anchor(x):
            continue
        if t == "Span" or (t == "Link" and (_is_in_book_target(x["c"][2][0]) or _is_filler(x))):
            pending.extend(reversed(x["c"][1]))
        else:
            if t == "Link":
                x["c"][0] = ["", [], []]
            out.append(x)
    # Re-join text split by an unwrapped element, as pandoc's readers would:
    # the Markdown writer only escapes a leading "4." or "#" it sees in one Str.
    merged = []
    for x in out:
        if merged and x.get("t") == "Str" and merged[-1].get("t") == "Str":
            merged[-1] = {"t": "Str", "c": merged[-1]["c"] + x["c"]}
        else:
            merged.append(x)
    return merged


def _clear_header_attributes(node):
    if isinstance(node, dict):
        if node.get("t") == "Header":
            node["c"][1] = ["", [], []]
        if isinstance(node.get("c"), list):
            _clear_header_attributes(node["c"])
    elif isinstance(node, list):
        for item in node:
            _clear_header_attributes(item)


def clean_calibre_ast(doc):
    """Structural cleanup of a pandoc JSON AST read from Calibre's HTML, in place.

    - EPUB footnotes become real pandoc notes (see convert_footnotes()).
    - In-book links (`#anchor` or a relative file such as `Cover.xhtml#c`) lose
      their targets but keep their text: what they point at does not survive
      into the translated book.
    - Spans are unwrapped, so `{lang="…"}` and class attributes never reach
      the Markdown.
    - Empty anchors inside text (`<a id="page_12"/>`) are dropped.
    - Heading and link attributes are cleared, whatever their id or class
      (external links keep their URL and title).
    """
    blocks = doc.get("blocks", [])
    footnotes = convert_footnotes(blocks)
    _map_inline_lists(blocks, _unwrap_inline_markup)
    _clear_header_attributes(blocks)
    return footnotes


def _pandoc_html_to_json(html_file):
    """Read an HTML file into a pandoc JSON AST string.

    Uses pypandoc when it is installed and falls back to the pandoc binary, so
    a machine with pandoc but without the Python wrapper still converts.
    """
    try:
        import pypandoc
    except ImportError:
        pypandoc = None
    if pypandoc is not None:
        return pypandoc.convert_file(html_file, 'json')
    result = subprocess.run(
        ['pandoc', html_file, '-f', 'html', '-t', 'json'],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def _pandoc_json_to_markdown(json_text, md_file, extra_args):
    """Write a pandoc JSON AST string to md_file as pandoc Markdown."""
    try:
        import pypandoc
    except ImportError:
        pypandoc = None
    if pypandoc is not None:
        pypandoc.convert_text(
            json_text, 'markdown-smart', format='json',
            outputfile=md_file, extra_args=extra_args,
        )
        return
    subprocess.run(
        ['pandoc', '-f', 'json', '-t', 'markdown-smart', '-o', md_file, *extra_args],
        input=json_text, capture_output=True, text=True, check=True,
    )


def convert_html_to_markdown(html_file, md_file, strip_page_numbers=False, detect_page_numbers=True):
    """Convert HTML to Markdown using pandoc (pypandoc, or the pandoc binary)"""
    try:
        doc = json.loads(_pandoc_html_to_json(html_file))
        footnotes = clean_calibre_ast(doc)
        if footnotes:
            print(f"Converted {footnotes} footnote(s) to Markdown footnotes")
        _pandoc_json_to_markdown(
            json.dumps(doc),
            md_file,
            # Without smart, typographic dashes and quotes stay Unicode instead
            # of becoming ---, -- and backslash-escaped ASCII quotes.
            # block puts each note right after the paragraph citing it, so a
            # note and its reference usually land in the same chunk.
            ['--wrap=none', '--reference-location=block'],
        )

        if os.path.exists(md_file):
            with open(md_file, 'r', encoding='utf-8') as f:
                content = f.read()

            content = content.replace('\ufeff', '')
            content = content.replace('\u00a0', ' ')
            content = clean_calibre_markers(
                content,
                strip_page_numbers=strip_page_numbers,
                detect_page_numbers=detect_page_numbers,
            )

            with open(md_file, 'w', encoding='utf-8') as f:
                f.write(content)

            print(f"Markdown conversion successful: {md_file}")
            return True
        else:
            print("Markdown file was not created")
            return False
    except FileNotFoundError:
        print("Neither pypandoc nor the pandoc binary was found. "
              "Install pandoc (https://pandoc.org/installing.html)")
        return False
    except subprocess.CalledProcessError as e:
        print(f"HTML to Markdown conversion failed: {(e.stderr or '').strip()[-500:]}")
        return False
    except Exception as e:
        print(f"HTML to Markdown conversion failed: {e}")
        return False


_PAGE_SEQUENCE_MIN_LENGTH = 4
_PAGE_SEQUENCE_MIN_RATIO = 0.5


def _detect_page_number_lines(lines):
    """Detect standalone-digit lines that form a monotonic page-number sequence.

    Returns a set of line indices that should be dropped as page numbers.

    Algorithm: collect every standalone-digit line in document order, find the
    Longest Non-Decreasing Subsequence (LNDS) of their integer values via
    bisect_right with parent-pointer reconstruction. If the LNDS is long enough
    and covers a large enough fraction of all standalone digits, treat those
    elements as page numbers. Outliers (years like 1984, chapter numbers,
    citation indices) sit off the monotonic spine and stay preserved.
    """
    digit_indices = []
    digit_values = []
    for i, line in enumerate(lines):
        s = line.strip()
        if s.isdigit():
            digit_indices.append(i)
            digit_values.append(int(s))

    n = len(digit_values)
    if n < _PAGE_SEQUENCE_MIN_LENGTH:
        return set()

    tails = []
    tails_idx = []
    parents = [-1] * n

    for i, v in enumerate(digit_values):
        pos = bisect.bisect_right(tails, v)
        if pos > 0:
            parents[i] = tails_idx[pos - 1]
        if pos == len(tails):
            tails.append(v)
            tails_idx.append(i)
        else:
            tails[pos] = v
            tails_idx[pos] = i

    lnds = []
    cur = tails_idx[-1]
    while cur != -1:
        lnds.append(cur)
        cur = parents[cur]
    lnds.reverse()

    if len(lnds) < _PAGE_SEQUENCE_MIN_LENGTH:
        return set()
    if len(lnds) / n < _PAGE_SEQUENCE_MIN_RATIO:
        return set()

    return {digit_indices[i] for i in lnds}


_CODE_FENCE_RE = re.compile(r'^(`{3,}|~{3,})')


def _fenced_code_line_indices(lines):
    found = set()
    fence = None
    for i, line in enumerate(lines):
        match = _CODE_FENCE_RE.match(line)
        if fence:
            found.add(i)
            if (match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence)
                    and not line[match.end():].strip()):
                fence = None
        elif match:
            fence = match.group(1)
            found.add(i)
    return found


def _is_empty_anchor_line(line):
    """True for a body line that only holds empty anchors, e.g. `[]` or `[]{#page_3}`.

    Indented lines are code, never anchors; fenced code is excluded by the caller.
    """
    if line[:1].isspace():
        return False
    residue = re.sub(r'\{[^}]*\}|\(#[^)\s]*\)', '', line)
    return '[' in residue and not residue.strip('[] \t')


def clean_calibre_markers(content, strip_page_numbers=False, detect_page_numbers=True):
    """Clean up Calibre-specific markers from markdown content.

    With detect_page_numbers, standalone digit lines are handled in two layers:
      1. If a line is adjacent to Calibre noise (::: fence, .ct}/.cn} marker),
         drop it — clearly leftover.
      2. Otherwise, run LNDS over all standalone digits to detect a monotonic
         page-number sequence and drop those. Outliers like years (1984),
         chapter numbers, and citation indices stay preserved.

    detect_page_numbers=False keeps every standalone digit line: it is meant
    for formats whose page numbers are not text (EPUB anchors, DOCX footers),
    where such lines are chapter, verse or exercise numbers.

    Pass strip_page_numbers=True to bypass both layers and aggressively delete
    every standalone-digit line (legacy behavior).

    Lines holding only empty anchors are dropped as well, but they still count
    as neighbours for layer 1.
    """
    content = re.sub(r'\{\.calibre[^}]*\}', '', content)
    content = re.sub(r'\(#calibre_link-\d+\)', '', content)

    # Clean heading calibre attribute blocks: {#calibre_link-N .calibreN}
    content = re.sub(r'\s*\{#calibre_link-\d+[^}]*\}', '', content)

    # Clean [**text**] format to **text**
    content = re.sub(r'\[\*\*([^*]+)\*\*\]', r'**\1**', content)

    lines = content.split('\n')

    page_number_lines = set() if strip_page_numbers else _detect_page_number_lines(lines)
    code_lines = _fenced_code_line_indices(lines)

    def is_calibre_noise(line):
        s = line.strip()
        if not s:
            return False
        if s.startswith(':::'):
            return True
        if s.endswith('.ct}') or s.endswith('.cn}'):
            return True
        return False

    def prev_nonblank(idx):
        for j in range(idx - 1, -1, -1):
            if lines[j].strip():
                return lines[j]
        return None

    def next_nonblank(idx):
        for j in range(idx + 1, len(lines)):
            if lines[j].strip():
                return lines[j]
        return None

    cleaned_lines = []
    for i, line in enumerate(lines):
        stripped_line = line.strip()

        if stripped_line.startswith(':::'):
            continue
        if stripped_line.endswith('.ct}') or stripped_line.endswith('.cn}'):
            continue
        if i not in code_lines and _is_empty_anchor_line(line):
            continue

        if re.match(r'^\s*\d+\s*$', line):
            if strip_page_numbers:
                continue
            if detect_page_numbers:
                if i in page_number_lines:
                    continue
                prev = prev_nonblank(i)
                nxt = next_nonblank(i)
                if (prev is not None and is_calibre_noise(prev)) or \
                   (nxt is not None and is_calibre_noise(nxt)):
                    continue
            # else: preserve as real content

        cleaned_lines.append(line)

    content = '\n'.join(cleaned_lines)
    content = re.sub(r'\n{3,}', '\n\n', content)
    return content


# =============================================================================
# Markdown input: pre-extracted Markdown (e.g. from MinerU / Marker / docling) skips Calibre
# =============================================================================

MARKDOWN_EXTENSIONS = ('.md', '.markdown')
CALIBRE_CONVERSION_METHOD = "calibre_htmlz"
MARKDOWN_CONVERSION_METHOD = "markdown"

_MD_IMG_RE = re.compile(r'(!\[[^\]]*\]\(\s*)(<[^>]+>|[^)\s]+)')
_HTML_IMG_RE = re.compile(r'(<img\b[^>]*?\bsrc\s*=\s*)(["\'])(.*?)\2', re.IGNORECASE | re.DOTALL)
_DATA_URI_RE = re.compile(r'^data:image/([A-Za-z0-9.+-]+);base64,(.*)$', re.DOTALL)
_URI_SCHEME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9+.-]*:')
_DATA_URI_EXTENSIONS = {
    "jpeg": ".jpg", "jpg": ".jpg", "png": ".png", "gif": ".gif",
    "webp": ".webp", "svg+xml": ".svg", "bmp": ".bmp",
}
# Comments a parser leaves where it dropped content; translation cannot bring it back.
_FORMULA_PLACEHOLDER = "<!-- formula-not-decoded -->"
_PARSER_PLACEHOLDERS = (
    (_FORMULA_PLACEHOLDER, "formula(s) were not decoded",
     "re-extract with formula recognition on (docling: --enrich-formula)"),
    ("<!-- image -->", "image(s) were exported as placeholders",
     "re-extract with images embedded or referenced (docling: --image-export-mode embedded)"),
)
# Formula models such as docling's write `&` and `\\` rows outside any
# environment. Pandoc's TeX reader rejects them and prints the formula as raw TeX.
# A $$...$$ block on lines of its own; like pandoc, never across a blank line.
_DISPLAY_MATH_RE = re.compile(r'^([ \t]*\$\$)((?:(?!\n[ \t]*\n)[^$])+?)(\$\$[ \t]*)$', re.MULTILINE)
_TEX_ENV_RE = re.compile(r'\\begin\{([^}]*)\}.*?\\end\{\1\}', re.DOTALL)
_TEX_ALIGN_TOKEN_RE = re.compile(r'\\\\|\\.|&', re.DOTALL)
# `$...$` as pandoc reads it: no space just inside either dollar, no digit right after the closing one.
_INLINE_MATH_RE = re.compile(r'(?<![$\\])\$(?![\s$])(?:\\.|[^$\\\n])+?(?<!\s)\$(?![$\d])')
# A document with only a formula or two may well have no inline math.
_INLINE_MATH_CHECK_MIN_DISPLAY = 3

_FRONT_MATTER_RE = re.compile(r'\A---[ \t]*\n(.*?)\n(?:---|\.\.\.)[ \t]*(?:\n|\Z)', re.DOTALL)
_FRONT_MATTER_KEYS = {
    "title": "title",
    "author": "creator",
    "creator": "creator",
    "publisher": "publisher",
    "lang": "language",
    "language": "language",
}


def _unquote_yaml_scalar(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def split_front_matter(content):
    """Split a leading YAML front matter block off content.

    Returns (metadata, body). Only flat `key: value` scalars and `- item`
    lists for the keys in _FRONT_MATTER_KEYS are read; anything fancier is
    ignored. A leading `---` block without any `key:` line is left alone,
    since it is more likely a thematic break than metadata.
    """
    match = _FRONT_MATTER_RE.match(content)
    if not match:
        return {}, content
    lines = match.group(1).split('\n')
    if not any(re.match(r'^[A-Za-z_][\w-]*\s*:', line) for line in lines):
        return {}, content

    metadata = {}
    current_key = None
    for line in lines:
        key_match = re.match(r'^([A-Za-z_][\w-]*)\s*:\s*(.*)$', line)
        if key_match:
            key = _FRONT_MATTER_KEYS.get(key_match.group(1).lower())
            value = _unquote_yaml_scalar(key_match.group(2))
            current_key = None
            if key and key not in metadata:
                if value:
                    metadata[key] = value
                else:
                    current_key = key  # value follows as a `- item` list
            continue
        item_match = re.match(r'^\s+-\s+(.+)$', line)
        if current_key and item_match:
            item = _unquote_yaml_scalar(item_match.group(1))
            if item:
                existing = metadata.get(current_key)
                metadata[current_key] = f"{existing}, {item}" if existing else item
    return metadata, content[match.end():]


def _first_h1_title(content):
    """Return the text of the document's first heading if it is level 1.

    Layout parsers put a paper's title first, as `# Title`. If the first
    heading is deeper than H1 it is a section, not the title, so return None.
    """
    in_fence = False
    for line in content.split('\n'):
        stripped = line.strip()
        if stripped.startswith('```'):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = re.match(r'^(#{1,6})\s+(.*?)\s*#*\s*$', stripped)
        if heading:
            if len(heading.group(1)) != 1:
                return None
            title = re.sub(r'\s*\{[^}]*\}$', '', heading.group(2)).strip()
            return title or None
    return None


def markdown_metadata(content):
    """Book metadata for Markdown input: front matter first, then the first H1."""
    metadata, body = split_front_matter(content.replace('﻿', ''))
    if 'title' not in metadata:
        title = _first_h1_title(body)
        if title:
            metadata['title'] = title
    return metadata


def align_display_math(content):
    r"""Wrap display formulas that have bare `&` or `\\` rows in an aligned environment.

    Returns (content, number of formulas wrapped). Fenced code is left alone.
    """
    wrapped = 0

    def wrap(m):
        nonlocal wrapped
        tokens = _TEX_ALIGN_TOKEN_RE.findall(_TEX_ENV_RE.sub('', m.group(2)))
        if '&' not in tokens and '\\\\' not in tokens:
            return m.group(0)
        wrapped += 1
        return f"{m.group(1)}\\begin{{aligned}}{m.group(2)}\\end{{aligned}}{m.group(3)}"

    lines = content.split('\n')
    code = _fenced_code_line_indices(lines)
    parts, start = [], 0
    for i in range(len(lines) + 1):
        if i == len(lines) or i in code:
            if start < i:
                parts.append(_DISPLAY_MATH_RE.sub(wrap, '\n'.join(lines[start:i])))
            if i < len(lines):
                parts.append(lines[i])
            start = i + 1
    return '\n'.join(parts), wrapped


def formula_counts(content):
    """Return (display formulas, inline formulas) outside fenced code.

    Placeholders for formulas a parser could not decode count as display formulas.
    """
    lines = content.split('\n')
    code = _fenced_code_line_indices(lines)
    text = '\n'.join(line for i, line in enumerate(lines) if i not in code)
    display = len(_DISPLAY_MATH_RE.findall(text)) + text.count(_FORMULA_PLACEHOLDER)
    inline = len(_INLINE_MATH_RE.findall(_DISPLAY_MATH_RE.sub('', text)))
    return display, inline


def _is_within(path, root):
    """True if path is root or lies below it. Pass both through os.path.realpath first."""
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:  # on Windows, paths on different drives
        return False


def import_markdown(md_path, temp_dir, base_dir=None):
    r"""Return Markdown content ready for chunking, with images moved under temp_dir/images/.

    Image paths, relative or absolute, are resolved against base_dir (by
    default the Markdown file's directory; a copy of the book is read with its
    original directory) and copied in only if they lie inside that directory tree once
    `..` and symlinks are resolved, so a Markdown file cannot pull other local
    files into the book. Base64 data URIs (MinerU and docling inline images
    that way) are decoded to files so chunks handed to translators never carry
    image payloads. Remote URLs stay untouched. YAML front matter is dropped
    from the body — its metadata goes to config.txt via markdown_metadata().
    Display formulas with bare `&` / `\\` rows are wrapped so pandoc can render
    them. Placeholders a parser left for dropped formulas or images are reported,
    and so are display formulas without any inline math, the mark of a parser
    that turned inline math into plain text.
    """
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()
    content = content.replace('﻿', '').replace(' ', ' ')
    _, content = split_front_matter(content)

    md_dir = os.path.abspath(base_dir) if base_dir else os.path.dirname(os.path.abspath(md_path))
    md_root = os.path.realpath(md_dir)
    images_dir = os.path.join(temp_dir, "images")
    refs = {}
    stored = {}
    embedded_count = 0

    def store(name, data):
        base, ext = os.path.splitext(name)
        digest = hashlib.sha256(data).hexdigest()
        candidate, n = name, 1
        while candidate in stored and stored[candidate] != digest:
            n += 1
            candidate = f"{base}-{n}{ext}"
        if candidate not in stored:
            os.makedirs(images_dir, exist_ok=True)
            with open(os.path.join(images_dir, candidate), 'wb') as out:
                out.write(data)
            stored[candidate] = digest
        return f"images/{candidate}"

    def localize(target):
        nonlocal embedded_count
        raw = target[1:-1] if target.startswith('<') and target.endswith('>') else target
        if raw in refs:
            return refs[raw]
        new_ref = target
        data_uri = _DATA_URI_RE.match(raw)
        if data_uri:
            try:
                data = base64.b64decode(data_uri.group(2))
            except (binascii.Error, ValueError):
                data = b''
            if data:
                subtype = data_uri.group(1).lower()
                ext = _DATA_URI_EXTENSIONS.get(subtype, '.' + re.sub(r'[^a-z0-9]', '', subtype))
                embedded_count += 1
                new_ref = store(f"embedded-{embedded_count:04d}{ext}", data)
            else:
                print("Warning: could not decode an embedded data-URI image; left inline")
        elif not _URI_SCHEME_RE.match(raw) and not raw.startswith('#'):
            path = os.path.normpath(os.path.join(md_dir, urllib.parse.unquote(raw)))
            src = os.path.realpath(path)
            if not _is_within(src, md_root):
                print("Warning: image outside the Markdown file's directory, not copied, "
                      f"reference kept as-is: {raw}")
            elif os.path.isfile(src):
                with open(src, 'rb') as f:
                    new_ref = store(os.path.basename(path), f.read())
            else:
                print(f"Warning: image not found next to the Markdown file, reference kept as-is: {raw}")
        refs[raw] = new_ref
        return new_ref

    content = _MD_IMG_RE.sub(lambda m: m.group(1) + localize(m.group(2)), content)
    content = _HTML_IMG_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{localize(m.group(3))}{m.group(2)}", content
    )

    if stored:
        print(f"Localized {len(stored)} image(s) into {images_dir}/")
    content, aligned = align_display_math(content)
    if aligned:
        print(f"Wrapped {aligned} display formula(s) with bare & or \\\\ rows in an aligned environment")
    for placeholder, what, fix in _PARSER_PLACEHOLDERS:
        count = content.count(placeholder)
        if count:
            print(f"Warning: {count} {what} ({placeholder}); {fix}")
    display, inline = formula_counts(content)
    if display >= _INLINE_MATH_CHECK_MIN_DISPLAY and not inline:
        print(f"Warning: {display} display formula(s) but no inline math; the parser may have "
              "turned inline math into plain text (docling's standard pipeline does); "
              "MinerU and Marker keep it as LaTeX")
    return content


def import_markdown_input(input_file, input_md, temp_dir, original=None):
    """Copy a Markdown source into temp_dir/input.md with its images localized.

    original is the book file when input_file is a copy of it: its directory
    is where the images are, and error messages name it.
    """
    os.makedirs(temp_dir, exist_ok=True)
    base_dir = os.path.dirname(os.path.abspath(original)) if original else None
    content = import_markdown(input_file, temp_dir, base_dir)
    if not content.strip():
        print(f"Error: {original or input_file} has no content to translate")
        return False
    with open(input_md, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"Markdown import successful: {input_md}")
    return True


# =============================================================================
# Structural block parsing and chunk splitting (Step 3)
# =============================================================================

# Pandoc footnote labels cannot contain whitespace, ^, [ or ].
_FOOTNOTE_DEF_RE = re.compile(r'^ {0,3}\[\^[^\]\s^\[]+\]:')


def _is_indented_text(line):
    return line.startswith(('    ', '\t')) and bool(line.strip())


def parse_structural_blocks(content):
    """Parse markdown into structural blocks that should not be split.

    Returns list of (text, block_type) tuples where block_type is one of:
    'heading', 'code_block', 'math_block', 'table', 'list', 'blockquote',
    'footnote', 'image', 'paragraph'
    """
    blocks = []
    lines = content.split('\n')
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Code block (fenced)
        if stripped.startswith('```'):
            block_lines = [line]
            i += 1
            while i < len(lines):
                block_lines.append(lines[i])
                if lines[i].strip().startswith('```') and len(block_lines) > 1:
                    i += 1
                    break
                i += 1
            blocks.append(('\n'.join(block_lines), 'code_block'))
            continue

        # Display math ($$ ... $$). Lines inside may look like list items or
        # tables, so the block must be kept whole. Like pandoc, never pair
        # delimiters across a blank line — an unclosed $$ must not swallow
        # the rest of the chapter.
        if stripped.startswith('$$'):
            if len(stripped) >= 4 and stripped.endswith('$$'):
                blocks.append((line, 'math_block'))
                i += 1
                continue
            end = None
            for j in range(i + 1, len(lines)):
                s = lines[j].strip()
                if not s:
                    break
                if s.endswith('$$'):
                    end = j
                    break
            if end is not None:
                blocks.append(('\n'.join(lines[i:end + 1]), 'math_block'))
                i = end + 1
                continue

        # Heading
        if re.match(r'^#{1,6}\s', stripped):
            blocks.append((line, 'heading'))
            i += 1
            continue

        # Blockquote
        if stripped.startswith('>'):
            block_lines = [line]
            i += 1
            while i < len(lines) and (lines[i].strip().startswith('>') or
                                       (lines[i].strip() and not re.match(r'^#{1,6}\s', lines[i].strip())
                                        and not lines[i].strip().startswith('```')
                                        and not lines[i].strip().startswith('|')
                                        and not re.match(r'^[-*+]\s', lines[i].strip())
                                        and not re.match(r'^\d+\.\s', lines[i].strip())
                                        and block_lines[-1].strip().startswith('>'))):
                block_lines.append(lines[i])
                i += 1
            blocks.append(('\n'.join(block_lines), 'blockquote'))
            continue

        # Table (lines starting with |)
        if stripped.startswith('|'):
            block_lines = [line]
            i += 1
            while i < len(lines) and lines[i].strip().startswith('|'):
                block_lines.append(lines[i])
                i += 1
            blocks.append(('\n'.join(block_lines), 'table'))
            continue

        # List (unordered or ordered)
        if re.match(r'^[-*+]\s', stripped) or re.match(r'^\d+\.\s', stripped):
            block_lines = [line]
            i += 1
            while i < len(lines):
                s = lines[i].strip()
                # Continue list: list items, indented continuation, or blank lines within list
                if (re.match(r'^[-*+]\s', s) or re.match(r'^\d+\.\s', s) or
                        (lines[i].startswith('  ') and s) or
                        (s == '' and i + 1 < len(lines) and
                         (re.match(r'^[-*+]\s', lines[i+1].strip()) or
                          re.match(r'^\d+\.\s', lines[i+1].strip()) or
                          lines[i+1].startswith('  ')))):
                    block_lines.append(lines[i])
                    i += 1
                else:
                    break
            blocks.append(('\n'.join(block_lines), 'list'))
            continue

        # Footnote definition plus its indented continuation paragraphs. Kept
        # whole: merging strips each chunk, which would un-indent a
        # continuation that started the next chunk and detach it from its note.
        if _FOOTNOTE_DEF_RE.match(line):
            block_lines = [line]
            i += 1
            while i < len(lines):
                if _is_indented_text(lines[i]) or (
                    not lines[i].strip() and i + 1 < len(lines) and _is_indented_text(lines[i + 1])
                ):
                    block_lines.append(lines[i])
                    i += 1
                else:
                    break
            blocks.append(('\n'.join(block_lines), 'footnote'))
            continue

        # Image line (standalone or with surrounding caption)
        if re.match(r'!\[', stripped):
            blocks.append((line, 'image'))
            i += 1
            continue

        # Empty line — just a paragraph separator
        if stripped == '':
            blocks.append((line, 'paragraph'))
            i += 1
            continue

        # Regular paragraph — collect contiguous non-empty, non-special lines
        block_lines = [line]
        i += 1
        while i < len(lines):
            s = lines[i].strip()
            if (s == '' or s.startswith('```') or s.startswith('$$') or re.match(r'^#{1,6}\s', s) or
                    s.startswith('>') or s.startswith('|') or
                    re.match(r'^[-*+]\s', s) or re.match(r'^\d+\.\s', s) or
                    re.match(r'!\[', s)):
                break
            block_lines.append(lines[i])
            i += 1
        blocks.append(('\n'.join(block_lines), 'paragraph'))
        continue

    return blocks


def merge_blocks_to_chunks(blocks, target_size=6000):
    """Merge structural blocks into chunks respecting target_size.

    Prefers to split at heading boundaries. Never splits within a single
    structural block unless the block itself exceeds target_size * 2.
    """
    chunks = []
    current_parts = []
    current_size = 0

    def flush():
        nonlocal current_parts, current_size
        if current_parts:
            chunks.append('\n'.join(current_parts))
            current_parts = []
            current_size = 0

    for text, btype in blocks:
        block_size = len(text)

        # If a single block is oversized, handle degradation
        if block_size > target_size * 2:
            flush()
            if btype == 'footnote':
                # A continuation paragraph cut loose from its `[^N]:` line
                # loses its indent when chunks are merged and becomes body text.
                print(f"  WARNING: Oversized footnote ({block_size} chars), kept in one chunk")
                chunks.append(text)
                continue
            print(f"  WARNING: Oversized {btype} block ({block_size} chars), force-splitting")
            sub_chunks = _force_split_block(text, target_size)
            chunks.extend(sub_chunks)
            continue

        # Prefer to split at heading boundaries
        if btype == 'heading' and current_size > 0:
            flush()

        # Would adding this block exceed target?
        if current_size + block_size > target_size and current_parts:
            flush()

        current_parts.append(text)
        current_size += block_size

    flush()
    # The blank line after a final oversized block would otherwise be its own
    # chunk, and an empty translation of it fails the merge.
    return [chunk for chunk in chunks if chunk.strip()]


def _force_split_block(text, target_size):
    """Force-split an oversized block by paragraph (empty lines), then by lines.

    For fenced code blocks, each resulting chunk gets proper opening/closing fences
    so it remains valid Markdown.
    """
    stripped = text.strip()
    is_fenced_code = stripped.startswith('```')

    # Extract fence info for code blocks
    fence_opener = ''
    if is_fenced_code:
        first_line = stripped.split('\n', 1)[0]
        fence_opener = first_line  # e.g. "```python"

    # Try splitting by empty lines first (not applicable for code blocks — no empty lines expected)
    if not is_fenced_code:
        paragraphs = re.split(r'\n\n+', text)
        if len(paragraphs) > 1:
            chunks = []
            current = []
            current_size = 0
            for para in paragraphs:
                para_size = len(para)
                if current_size + para_size > target_size and current:
                    chunks.append('\n\n'.join(current))
                    current = [para]
                    current_size = para_size
                else:
                    current.append(para)
                    current_size += para_size
            if current:
                chunks.append('\n\n'.join(current))
            return chunks

    # Split by lines
    lines = text.split('\n')

    # For code blocks, strip the opening and closing fences before splitting content
    if is_fenced_code:
        # Remove opening fence line
        content_lines = lines[1:]
        # Remove closing fence line if present
        if content_lines and content_lines[-1].strip().startswith('```'):
            content_lines = content_lines[:-1]
        lines = content_lines

    chunks = []
    current = []
    current_size = 0
    for line in lines:
        line_size = len(line) + 1
        if current_size + line_size > target_size and current:
            chunks.append('\n'.join(current))
            current = [line]
            current_size = line_size
        else:
            current.append(line)
            current_size += line_size
    if current:
        chunks.append('\n'.join(current))

    # Re-wrap each chunk in fences for code blocks
    if is_fenced_code:
        chunks = [f"{fence_opener}\n{chunk}\n```" for chunk in chunks]

    return chunks


def split_markdown_structured(md_file, temp_dir, target_size=6000):
    """Split markdown into structural chunks.

    Returns list of chunk filenames (e.g. ['chunk0001.md', ...]).
    """
    try:
        with open(md_file, 'r', encoding='utf-8') as f:
            content = f.read()

        blocks = parse_structural_blocks(content)
        chunk_texts = merge_blocks_to_chunks(blocks, target_size)

        chunk_files = []
        for i, chunk_text in enumerate(chunk_texts, 1):
            filename = chunk_file_name(nth_chunk_id(i))
            chunk_file = os.path.join(temp_dir, filename)
            with open(chunk_file, 'w', encoding='utf-8') as f:
                f.write(chunk_text)
            chunk_files.append(filename)

        print(f"Split into {len(chunk_files)} chunks")
        for filename in chunk_files:
            filepath = os.path.join(temp_dir, filename)
            size = os.path.getsize(filepath)
            print(f"  {filename}: {size} characters")

        return chunk_files
    except Exception as e:
        print(f"Error splitting markdown: {e}")
        return []


def _find_existing_chunk_files(temp_dir):
    """Existing chunk source file names in chunk order; raises ValueError for stray chunk*.md names."""
    return list_chunk_files(temp_dir)


def _config_value(value):
    """A value as config.txt can hold it on one line: control characters such as
    line breaks become spaces, and undecodable file name bytes become U+FFFD."""
    return ''.join(
        ' ' if unicodedata.category(ch) == 'Cc' else '\ufffd' if unicodedata.category(ch) == 'Cs' else ch
        for ch in str(value)
    )


def create_config_file(temp_dir, input_file, input_lang, output_lang, metadata=None,
                       conversion_method=CALIBRE_CONVERSION_METHOD, parser=None):
    """Create config.txt file for the pipeline"""
    try:
        config_file = os.path.join(temp_dir, "config.txt")

        config_content = f"""# Translation Configuration
input_file={_config_value(input_file)}
input_lang={_config_value(input_lang)}
output_lang={_config_value(output_lang)}
conversion_method={conversion_method}
"""
        if parser:
            config_content += f"parser={parser}\n"
        if metadata:
            config_content += f"\n# Book Metadata\n"
            if 'title' in metadata:
                config_content += f"original_title={_config_value(metadata['title'])}\n"
            if 'creator' in metadata:
                config_content += f"creator={_config_value(metadata['creator'])}\n"
            if 'publisher' in metadata:
                config_content += f"publisher={_config_value(metadata['publisher'])}\n"
            if 'language' in metadata:
                config_content += f"source_language={_config_value(metadata['language'])}\n"

        with open(config_file, 'w', encoding='utf-8') as f:
            f.write(config_content)

        print(f"Created config file: {config_file}")
        return True
    except Exception as e:
        print(f"Error creating config file: {e}")
        return False


def _do_split_and_manifest(temp_dir, input_md, chunk_size):
    """Split markdown and create manifest. Returns chunk count or 0 on failure."""
    existing = _find_existing_chunk_files(temp_dir)
    if existing:
        print(f"Skipping markdown splitting - found {len(existing)} existing chunk files")
        # Create/update manifest for existing files
        create_manifest(temp_dir, existing, input_md)
        return len(existing)

    chunk_files = split_markdown_structured(input_md, temp_dir, chunk_size)
    if not chunk_files:
        return 0
    create_manifest(temp_dir, chunk_files, input_md)
    return len(chunk_files)


def _check_strip_page_numbers_cache_conflict(strip_flag, temp_dir, input_md):
    """Return cached files that would silently neutralize a page-number flag.

    --strip-page-numbers and --detect-page-numbers only take effect inside
    clean_calibre_markers, which runs during HTML→Markdown conversion. If
    input.md or chunk*.md already exist from a prior run, both are reused
    as-is and the flag becomes a no-op. Surface that conflict so the user
    knows to clean up.
    """
    if not strip_flag:
        return []
    if not os.path.isdir(temp_dir):
        return []

    blockers = []
    if os.path.exists(input_md):
        blockers.append(input_md)

    existing_chunks = list_chunk_files(temp_dir)
    if existing_chunks:
        blockers.append(f"{len(existing_chunks)} chunk file(s) under {temp_dir}/")

    return blockers


def _abort_on_strip_cache_conflict(blockers, temp_dir):
    if not blockers:
        return
    print("Error: page-number options cannot take effect because cached files exist:")
    for b in blockers:
        print(f"  - {b}")
    print(f"They act only while input.md is made, which already happened in {temp_dir}/. "
          f"Run without them to keep using its chunks, or set temp_root to another "
          f"directory to convert the book again with them.")
    sys.exit(1)


# =============================================================================
# Request file, PDF parsers and cover
# =============================================================================

# SKILL.md's Step 2 command is fixed text. Everything that varies (the book's
# path, the target language, temp_root, the cover, the parser) reaches this
# script inside a JSON file the agent writes with its file-writing tool.
REQUEST_FILE_NAME = "translate-book-request.json"
STORED_REQUEST_FILE = "request.json"
_REQUEST_KEYS = ("input", "target_lang", "temp_root", "cover", "parser")
_REQUEST_MAX_BYTES = 64 * 1024
INPUT_EXTENSIONS = ('.pdf', '.docx', '.epub') + MARKDOWN_EXTENSIONS
COVER_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp', '.gif')
_LANG_TAG_RE = re.compile(r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8})*')

# Layout-aware PDF parsers, each run as its own fixed executable with the
# fixed arguments of _parser_argv() and nothing else.
PARSERS = {
    "mineru": "mineru-kit",
    "marker": "marker_single",
    "docling": "docling",
}
PARSED_DIR = "parsed"


def normalize_target_lang(value):
    """Return a language tag such as zh, it, pt-BR or zh-TW (`_` read as `-`); raise ValueError otherwise."""
    tag = str(value).strip().replace('_', '-')
    if len(tag) > 35 or not _LANG_TAG_RE.fullmatch(tag):
        raise ValueError(f"target language {value!r} is not a language code such as zh, it, pt-BR or zh-TW")
    return tag


def check_input_file(path):
    """Return the absolute path of a convertible input file; raise ValueError otherwise."""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise ValueError(f"Input file not found: {path}")
    ext = os.path.splitext(path)[1].lower()
    if ext not in INPUT_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {ext or '(none)'}; use PDF, DOCX, EPUB or Markdown (.md, .markdown)")
    return path


def check_cover_file(path):
    """Return the absolute path of a cover image; raise ValueError otherwise."""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise ValueError(f"Cover image not found: {path}")
    ext = os.path.splitext(path)[1].lower()
    if ext not in COVER_EXTENSIONS:
        raise ValueError(f"Cover image {path} is not one of {', '.join(COVER_EXTENSIONS)}")
    return path


def _reject_duplicate_keys(pairs):
    keys = [key for key, _ in pairs]
    repeated = sorted({key for key in keys if keys.count(key) > 1})
    if repeated:
        raise ValueError(f"the key(s) {', '.join(map(repr, repeated))} appear more than once")
    return dict(pairs)


def _request_text(data, key, required=False):
    value = data.get(key)
    if value is None:
        if required:
            raise ValueError(f"{key!r} is required")
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key!r} must be a string" + ("" if required else " or null"))
    if not value.strip():
        raise ValueError(f"{key!r} is empty")
    bad = next((ch for ch in value if unicodedata.category(ch) in ('Cc', 'Cs')), None)
    if bad is not None and unicodedata.category(bad) == 'Cs':
        raise ValueError(f"{key!r} contains the lone surrogate \\u{ord(bad):04x}, which is not "
                         f"text; rename the file so that its name is valid UTF-8")
    if bad is not None:
        raise ValueError(f"{key!r} contains the control character U+{ord(bad):04X}; in JSON, "
                         f"write each backslash of a path as two backslashes")
    return value


def read_request(path):
    """Read and check a request file. Returns (raw bytes, request, read_from).

    request has every key of _REQUEST_KEYS: absolute input and cover paths, a
    normalized target_lang (default zh), a checked temp_root or None, and the
    parser or None. read_from is the file read, as an Own holding raw.
    Raises ValueError naming the problem; nothing is created or changed.
    """
    try:
        fd = os.open(path, os.O_RDONLY)
        read_from = Own(fd)
        with open(fd, 'rb', closefd=False) as f:
            raw = f.read(_REQUEST_MAX_BYTES + 1)
        read_from.data = raw
    except FileNotFoundError:
        raise ValueError(f"cannot read the request file {path}: it does not exist. Write it "
                         f"before each run: a successful run keeps a copy in the temp directory "
                         f"as {STORED_REQUEST_FILE} and removes the file it read unless that "
                         f"changed meanwhile") from None
    except OSError as e:
        raise ValueError(f"cannot read the request file {path}: {e.strerror}") from None
    if len(raw) > _REQUEST_MAX_BYTES:
        raise ValueError(f"{path} is larger than {_REQUEST_MAX_BYTES} bytes; a request holds a few paths")
    try:
        data = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_reject_duplicate_keys)
    except UnicodeDecodeError:
        raise ValueError(f"{path} is not UTF-8 text") from None
    except json.JSONDecodeError as e:
        raise ValueError(
            f"{path} is not valid JSON ({e.msg} at line {e.lineno}, column {e.colno}). Put double "
            f"quotes around keys and strings, write null rather than None, leave no comma after "
            f"the last item, and write each backslash of a path as two backslashes."
        ) from None
    except ValueError as e:
        raise ValueError(f"{path}: {e}") from None
    try:
        return raw, _check_request(data), read_from
    except ValueError as e:
        raise ValueError(f"{path}: {e}") from None


def _check_request(data):
    if not isinstance(data, dict):
        raise ValueError('it must hold a JSON object such as {"input": "/path/to/book.epub", "target_lang": "zh"}')
    if 'parser_args' in data:
        raise ValueError("'parser_args' is not supported: convert.py runs each parser with fixed "
                         "arguments only. To use other parser options, run the parser yourself "
                         "and give convert.py the Markdown file it writes as input, with parser null")
    unknown = sorted(set(data) - set(_REQUEST_KEYS))
    if unknown:
        raise ValueError(f"unknown key(s) {', '.join(map(repr, unknown))}; the keys are {', '.join(_REQUEST_KEYS)}")

    input_file = check_input_file(os.path.expanduser(_request_text(data, 'input', required=True)))
    target_lang = _request_text(data, 'target_lang')
    target_lang = normalize_target_lang(target_lang) if target_lang is not None else 'zh'
    temp_root = _request_text(data, 'temp_root')
    temp_root = validate_temp_root(temp_root) if temp_root is not None else None
    cover = _request_text(data, 'cover')
    cover = check_cover_file(os.path.expanduser(cover)) if cover is not None else None

    parser = data.get('parser')
    if parser is not None and (not isinstance(parser, str) or parser not in PARSERS):
        raise ValueError(f"parser {parser!r} is not one of {', '.join(PARSERS)} (or null)")
    if parser and os.path.splitext(input_file)[1].lower() != '.pdf':
        raise ValueError(f"parser {parser!r} only reads PDF input")
    return {
        "input": input_file,
        "target_lang": target_lang,
        "temp_root": temp_root,
        "cover": cover,
        "parser": parser,
    }


def check_stored_request(data):
    """Return data if it is a request as store_request() writes it; raise ValueError otherwise.

    Every key of _REQUEST_KEYS must be there, and nothing else, with the form
    _check_request() gives it. The files it names need not exist any more.
    """
    if not isinstance(data, dict) or set(data) != set(_REQUEST_KEYS):
        raise ValueError(f"it does not hold exactly the keys {', '.join(_REQUEST_KEYS)}")
    for key, extensions in (('input', INPUT_EXTENSIONS), ('cover', COVER_EXTENSIONS)):
        value = data[key]
        if value is None and key == 'cover':
            continue
        if not (isinstance(value, str) and os.path.isabs(value)
                and os.path.splitext(value)[1].lower() in extensions):
            raise ValueError(f"its {key} {value!r} is not an absolute path to a "
                             f"{'/'.join(extensions)} file")
    try:
        if normalize_target_lang(data['target_lang']) != data['target_lang']:
            raise ValueError
        temp_root = data['temp_root']
        if temp_root is not None and not (isinstance(temp_root, str)
                                          and validate_temp_root(temp_root) == temp_root):
            raise ValueError
    except ValueError:
        raise ValueError(f"its target_lang {data['target_lang']!r} or temp_root "
                         f"{data['temp_root']!r} is not in the form convert.py stores") from None
    if data['parser'] is not None and not (isinstance(data['parser'], str) and data['parser'] in PARSERS):
        raise ValueError(f"its parser {data['parser']!r} is not one of {', '.join(PARSERS)} or null")
    return data


def load_stored_request(temp_dir):
    """The request a successful run stored in temp_dir, or None when there is none.

    Raises ValueError when <temp_dir>/request.json is there but is not a regular
    file holding a request convert.py stored.
    """
    path = os.path.join(temp_dir, STORED_REQUEST_FILE)
    if not os.path.lexists(path):
        return None
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            raise ValueError("it is not a regular file")
        with open(path, 'rb') as f:
            data = json.loads(f.read(_REQUEST_MAX_BYTES + 1).decode('utf-8'),
                              object_pairs_hook=_reject_duplicate_keys)
        return check_stored_request(data)
    except (OSError, ValueError) as e:
        raise ValueError(f"{path} is not a request convert.py stored ({e})") from None


def _write_atomically(path, data):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or '.', prefix='.tmp.', suffix='.part')
    written = Own(fd)
    try:
        with open(fd, 'wb', closefd=False) as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        _remove_own_file(tmp, written, same_bytes=False)
        raise
    finally:
        written.close()


def store_request(temp_dir, request_path, request, read_from):
    """Keep the request as <temp_dir>/request.json and remove the file it was read from.

    The checked request is written to a new file inside temp_dir and renamed
    over request.json, which main() has checked is absent or a request an
    earlier run stored. The original is removed only if it is still the file
    this run read (read_from, from read_request()) and holds the bytes it
    read: a request written meanwhile, for another book or by another run,
    stays where it is.
    """
    stored = os.path.join(temp_dir, STORED_REQUEST_FILE)
    _write_atomically(stored, (json.dumps(request, indent=2, ensure_ascii=False) + '\n').encode('utf-8'))
    try:
        if os.path.samefile(request_path, stored):
            return
    except OSError:
        return
    _remove_own_file(request_path, read_from)


def _parser_argv(parser, executable, pdf, parsed_dir):
    if parser == "mineru":
        return [executable, "parse", pdf, "-o", os.path.join(parsed_dir, "book.md")]
    if parser == "marker":
        return [executable, pdf, "--output_dir", parsed_dir]
    return [executable, pdf, "--enrich-formula", "--output", parsed_dir]


def _manual_parser_route(parser):
    fixed = ' '.join(_parser_argv(parser, PARSERS[parser], '<pdf>', '<dir>'))
    return (f"convert.py runs {PARSERS[parser]} only as `{fixed}`. If your version of it "
            f"needs other arguments, run {PARSERS[parser]} yourself and give convert.py the "
            f"Markdown file it writes as input, with parser null.")


def _output_tail(log, lines=20):
    log.seek(0)
    return ''.join(log.read().decode('utf-8', errors='replace').splitlines(True)[-lines:]).rstrip()


def run_parser(parser, input_pdf, temp_dir):
    """Run a layout-aware parser on the PDF; return (Markdown file, parsed dir) or (None, None).

    The parser gets the fixed arguments of _parser_argv() and writes into
    <temp_dir>/parsed/, which this function creates and returns as an OwnDir;
    the caller removes it with remove() once it has read the Markdown. A
    parsed/ that already exists, even as a symlink or a file, is neither used
    nor changed. On failure the directory is removed here. Exactly one
    Markdown file must come out, a regular file inside parsed/.
    """
    executable = shutil.which(PARSERS[parser])
    if not executable:
        print(f"Error: {PARSERS[parser]} not found; install {parser} or set parser to null")
        return None, None
    path = os.path.join(temp_dir, PARSED_DIR)
    try:
        parsed = OwnDir.make(path)
    except FileExistsError:
        print(f"Error: {path} already exists. The parser writes only into a {PARSED_DIR}/ "
              f"that this run creates, so that one was neither used nor changed; rename or "
              f"move it, then run again.")
        return None, None
    try:
        markdown_file = _parse(parser, executable, input_pdf, os.path.abspath(path))
    except BaseException:
        parsed.remove()
        raise
    if markdown_file is None:
        parsed.remove()
        return None, None
    return markdown_file, parsed


def _parse(parser, executable, input_pdf, parsed):
    argv = _parser_argv(parser, executable, input_pdf, parsed)
    print(f"Running {PARSERS[parser]}; this can take many minutes.")
    with tempfile.TemporaryFile() as log:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, cwd=parsed)
        if result.returncode != 0:
            print(f"Error: {PARSERS[parser]} exited with status {result.returncode}. End of its "
                  f"output:\n{_output_tail(log)}\n{_manual_parser_route(parser)}")
            return None
    root = os.path.realpath(parsed)
    found = [os.path.join(dirpath, name)
             for dirpath, _, names in os.walk(parsed)
             for name in names if name.lower().endswith(MARKDOWN_EXTENSIONS)]
    usable = [p for p in found if not os.path.islink(p) and os.path.isfile(p)
              and _is_within(os.path.realpath(p), root)]
    if len(found) != 1 or len(usable) != 1:
        listed = ', '.join(repr(os.path.relpath(p, parsed)) for p in sorted(found)) or 'none'
        print(f"Error: expected one Markdown file from {PARSERS[parser]} in {parsed}, found "
              f"{listed}. {_manual_parser_route(parser)}")
        return None
    return usable[0]


def main():
    """Main conversion function"""
    parser = argparse.ArgumentParser(
        description="Convert PDF/DOCX/EPUB (via Calibre HTMLZ) or Markdown to markdown chunks"
    )
    parser.add_argument(
        "input_file", nargs="?",
        help="Input file: PDF, DOCX, or EPUB (converted by Calibre), or Markdown (.md/.markdown), "
             "e.g. the output of a layout-aware PDF parser such as MinerU, Marker or docling",
    )
    parser.add_argument(
        "--request", metavar="FILE",
        help=f"Read the input file and settings from a JSON request file such as {REQUEST_FILE_NAME} "
             f"(keys: {', '.join(_REQUEST_KEYS)}) instead of the command line. After a successful "
             f"conversion a copy is kept as <temp_dir>/{STORED_REQUEST_FILE}, and the file is "
             f"removed if it still holds what was read.",
    )
    parser.add_argument("-l", "--ilang", default="auto", help="Input language (default: auto)")
    parser.add_argument("--olang", default=None, help="Output language (default: zh)")
    parser.add_argument("--chunk-size", type=int, default=6000, help="Target chunk size in characters (default: 6000)")
    parser.add_argument(
        "--temp-root",
        default=None,
        help="Directory under which <name>_temp/ will be created; its path may hold letters, "
             "digits, '.', '_', '-' and '/' only (default: current working directory)",
    )
    parser.add_argument(
        "--strip-page-numbers",
        action="store_true",
        help="Aggressively delete every standalone-digit line (legacy behavior).",
    )
    parser.add_argument(
        "--detect-page-numbers",
        action="store_true",
        help="Drop detected page-number lines from EPUB/DOCX too. By default only "
             "PDF input is cleaned this way; EPUB/DOCX keep every standalone number.",
    )

    args = parser.parse_args()
    if args.request and (args.input_file or args.olang is not None or args.temp_root is not None):
        parser.error("with --request, give the input file, the language and temp_root in the request file")
    if not args.request and not args.input_file:
        parser.error("give an input file, or --request with a request file")

    try:
        if args.request:
            _, request, read_from = read_request(args.request)
        else:
            read_from, request = None, {
                "input": check_input_file(args.input_file),
                "target_lang": normalize_target_lang(args.olang if args.olang is not None else "zh"),
                "temp_root": validate_temp_root(args.temp_root) if args.temp_root else None,
                "cover": None,
                "parser": None,
            }
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    input_file = request["input"]
    target_lang = request["target_lang"]
    parser_name = request["parser"]
    file_ext = os.path.splitext(input_file)[1].lower()
    is_markdown = file_ext in MARKDOWN_EXTENSIONS
    page_number_flag = args.strip_page_numbers or args.detect_page_numbers
    if (is_markdown or parser_name) and page_number_flag:
        flag = "--strip-page-numbers" if args.strip_page_numbers else "--detect-page-numbers"
        print(f"Error: {flag} only cleans Calibre output; it does not apply to Markdown input "
              f"or parser output.")
        sys.exit(1)
    if parser_name and not shutil.which(PARSERS[parser_name]) and not os.path.exists(
            os.path.join(build_temp_dir(input_file, request["temp_root"]), "input.md")):
        print(f"Error: {PARSERS[parser_name]} not found; install {parser_name} or set parser to null")
        sys.exit(1)
    # Only PDFs carry page numbers as text. EPUB page numbers are invisible
    # anchors and Calibre drops DOCX footers, so their digit lines are content.
    detect_page_numbers = args.detect_page_numbers or file_ext == '.pdf'

    if is_markdown:
        print("=== Markdown Import (no Calibre) ===")
    elif parser_name:
        print(f"=== PDF Import via {PARSERS[parser_name]} ===")
    else:
        print("=== File Conversion via Calibre HTMLZ ===")
    print(f"Input file: {input_file}")
    print(f"Target chunk size: {args.chunk_size} characters")
    if request["temp_root"]:
        print(f"Temp root: {request['temp_root']}")

    calibre_path = None
    if not is_markdown and not parser_name and not any(
            os.path.isfile(os.path.join(d, "input.html")) for d in (
                build_temp_dir(input_file, request["temp_root"]),
                legacy_temp_dir(input_file, request["temp_root"]))):
        calibre_path = find_calibre_convert()
        if not calibre_path:
            print("Error: Calibre ebook-convert not found")
            print("Please install Calibre: https://calibre-ebook.com/")
            sys.exit(1)

    temp_dir = None
    succeeded = False
    scratch = None
    try:
        identity = run_identity(input_file, target_lang, parser_name)
        try:
            temp_dir, notes, claim = prepare_temp_dir(input_file, request["temp_root"], identity)
        except TempDirError as e:
            print(f"Error: {e}")
            sys.exit(1)
        if args.request:
            try:
                load_stored_request(temp_dir)
            except ValueError as e:
                print(f"Error: {e}. It is not replaced; rename or move it, then run again.")
                sys.exit(1)
        for note in notes:
            print(note)
        input_md = os.path.join(temp_dir, "input.md")
        conversion_method = CALIBRE_CONVERSION_METHOD
        # The copy of the book that conversion reads goes into a directory of
        # this run's own, outside the temp dir.
        scratch = OwnDir.make_temp("translate-book-")

        if is_markdown:
            conversion_method = MARKDOWN_CONVERSION_METHOD
            markdown_copy = copy_source(input_file, scratch, identity)
            with open(markdown_copy, 'r', encoding='utf-8') as f:
                metadata = markdown_metadata(f.read())
            if os.path.exists(input_md):
                print("Skipping Markdown import - input.md already exists")
            elif not import_markdown_input(markdown_copy, input_md, temp_dir, original=input_file):
                sys.exit(1)
        elif parser_name:
            conversion_method = MARKDOWN_CONVERSION_METHOD
            if os.path.exists(input_md):
                print("Skipping PDF parsing - input.md already exists")
                metadata = _config_metadata(temp_dir)
            else:
                markdown_file, parsed = run_parser(
                    parser_name, copy_source(input_file, scratch, identity), temp_dir)
                if not markdown_file:
                    sys.exit(1)
                try:
                    with open(markdown_file, 'r', encoding='utf-8') as f:
                        metadata = markdown_metadata(f.read())
                    if not import_markdown_input(markdown_file, input_md, temp_dir):
                        sys.exit(1)
                finally:
                    parsed.remove()
                # The parser's own Markdown is gone now; keep its metadata for re-runs.
                create_config_file(temp_dir, input_file, args.ilang, target_lang, metadata,
                                   conversion_method=conversion_method, parser=parser_name)
        elif os.path.exists(os.path.join(temp_dir, "input.html")):
            input_html_path = os.path.join(temp_dir, "input.html")
            print(f"Skipping HTMLZ conversion - input.html already exists")
            metadata = _config_metadata(temp_dir)
            if 'title' not in metadata:
                html_title = _html_title(input_html_path)
                if html_title:
                    metadata['title'] = html_title

            _abort_on_strip_cache_conflict(
                _check_strip_page_numbers_cache_conflict(page_number_flag, temp_dir, input_md),
                temp_dir,
            )
            if os.path.exists(input_md):
                print(f"Skipping HTML to Markdown conversion - input.md already exists")
            elif not convert_html_to_markdown(input_html_path, input_md,
                                              strip_page_numbers=args.strip_page_numbers,
                                              detect_page_numbers=detect_page_numbers):
                sys.exit(1)
        else:
            calibre_path = calibre_path or find_calibre_convert()
            if not calibre_path:
                print("Error: Calibre ebook-convert not found")
                print("Please install Calibre: https://calibre-ebook.com/")
                sys.exit(1)

            # Never next to the source: that path may be the user's own
            # <book>.htmlz, which Calibre would overwrite and we would delete.
            extract_dir = scratch.make_dir("htmlz")
            htmlz_file = os.path.join(extract_dir, "input.htmlz")
            if not convert_to_htmlz(copy_source(input_file, scratch, identity), htmlz_file,
                                    calibre_path):
                sys.exit(1)

            html_file, images_dir = extract_htmlz(htmlz_file, extract_dir)
            if not html_file:
                sys.exit(1)

            metadata = extract_metadata_from_htmlz(extract_dir)

            if not setup_temp_directory(temp_dir, html_file, images_dir):
                sys.exit(1)
            # Write the metadata now: later steps can fail (no pandoc, say), and
            # a re-run takes the cached input.html path, which only finds the
            # book's title and author in config.txt.
            create_config_file(temp_dir, input_file, args.ilang, target_lang, metadata)

            input_html = os.path.join(temp_dir, "input.html")
            _abort_on_strip_cache_conflict(
                _check_strip_page_numbers_cache_conflict(page_number_flag, temp_dir, input_md),
                temp_dir,
            )
            if os.path.exists(input_md):
                print(f"Skipping HTML to Markdown conversion - input.md already exists")
            elif not convert_html_to_markdown(input_html, input_md,
                                              strip_page_numbers=args.strip_page_numbers,
                                              detect_page_numbers=detect_page_numbers):
                sys.exit(1)

        chunk_count = _do_split_and_manifest(temp_dir, input_md, args.chunk_size)
        if chunk_count == 0:
            sys.exit(1)

        create_config_file(temp_dir, input_file, args.ilang, target_lang, metadata,
                           conversion_method=conversion_method, parser=parser_name)
        _write_source_fingerprint(temp_dir, identity).close()
        if args.request:
            store_request(temp_dir, args.request, request, read_from)
        succeeded = True

        print("Conversion completed successfully!")
        print(f"Temp directory: {temp_dir}")
        print(f"Markdown chunks: {chunk_count} files")

    except KeyboardInterrupt:
        print("\nConversion interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        if scratch is not None:
            scratch.remove()
        if temp_dir is not None and not succeeded:
            release_temp_dir(temp_dir, claim)
        if temp_dir is not None:
            claim.close()
        if read_from is not None:
            read_from.close()


if __name__ == "__main__":
    main()
