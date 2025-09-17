import logging
import os
import platform
import re
import shutil
import stat

import requests
import subprocess
import sys
from bs4 import BeautifulSoup
from pathlib import Path
from tqdm import tqdm

# ------------------------------ Parameters Configuration ------------------------------
APP_FILE = "Termius"
DIR_TMP = ".tmp_dir"
EXT_APKM = ".apkm"
EXT_APK = ".apk"
APKM_FILENAME = f"{APP_FILE}{EXT_APKM}"
APK_EDITOR_FILENAME = "APKEditor.jar"
LANGUAGE_XML = "strings.xml"
BASE_APK_URL = "https://www.apkmirror.com/apk/termius-corporation/termius-ssh-telnet-client/"  # APK mirror base URL
GITHUB_REPO_OWNER = "REAndroid"
GITHUB_REPO_NAME = "APKEditor"
APK_SIGN_PROPERTIES = "apk.sign.properties"
ALIGNED_SUFFIX = "_aligned"
SIGNED_SUFFIX = "_signed"
ZH_SUFFIX = "_zh"

# ------------------------------ Log Configuration ------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)-1.1s: %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36 Edg/138.0.0.0"
}


def is_windows():
    return platform.system() == 'Windows'


def windows_hide_file(file_path):
    run_command(f"attrib +h {file_path}")


def create_or_recreate_dir(dir_path):
    if os.path.exists(dir_path):
        if os.path.isdir(dir_path):
            safe_rmtree(dir_path)
        else:
            os.remove(dir_path)
    os.mkdir(dir_path)
    if is_windows():
        windows_hide_file(dir_path)


def create_tmp_dir(working_dir):
    tmp_dir = os.path.abspath(os.path.join(working_dir, DIR_TMP))
    create_or_recreate_dir(tmp_dir)
    return tmp_dir


def split_filename(abs_path):
    """Extract filename from absolute path and separate base name and extension."""
    # Extract full filename (with extension)
    full_filename = os.path.basename(abs_path)
    # Separate base name and extension
    base_name, ext = os.path.splitext(full_filename)
    return base_name, ext


def replace_file(source_path, target_path):
    """Safely replace target file"""
    if not os.path.exists(source_path):
        logger.error(f"Source file does not exist, cannot replace: {source_path}")
        return False

    target_exists = os.path.exists(target_path)
    if not target_exists:
        logger.warning(f"Target file does not exist, will copy directly: {target_path}")

    try:
        shutil.copy2(source_path, target_path)
        logger.info(f"File replaced successfully: {target_path}, [Source: {source_path}]")
        return True

    except PermissionError:
        logger.error(f"No write permission for target path: {target_path}, check directory permissions")
        return False
    except Exception as e:
        logger.error(f"File replacement failed: {str(e)}")
        return False


def run_command(cmd, shell=False, log=True):
    """Execute system command"""
    if log:
        logging.info(f"Executing command: {cmd}")
    try:
        return subprocess.run(cmd, shell=shell, check=True)
    except subprocess.CalledProcessError as e:
        logging.error(f"Command execution failed: {e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Execution error: {e}")
        sys.exit(1)


def _handle_remove_readonly(func, path, _):
    """Handle read-only files"""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def safe_rmtree(path):
    """Safely delete directory"""
    if not os.path.exists(path):
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_handle_remove_readonly)
    else:
        shutil.rmtree(path, onerror=_handle_remove_readonly)


def fetch_page(url, headers):
    """Fetch page content and return BeautifulSoup object"""
    try:
        response = requests.get(url, headers=headers, timeout=10)
        # Check HTTP status code (4xx/5xx will raise an exception)
        response.raise_for_status()
        return BeautifulSoup(response.text, 'html.parser')
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch page: {str(e)}")
        return None


def extract_version_from_title(soup):
    """Extract version number from page title (adapted for APK Mirror title format)"""
    title_selector = '#primary > div.listWidget.p-relative .appRow h5.appRowTitle'
    title_element = soup.select_one(title_selector)

    if not title_element:
        logger.error("Application title element not found, check selector compatibility")
        return None

    full_title = title_element.get_text(strip=True)
    version_match = re.search(r'v?(\d+\.\d+\.\d+)', full_title)

    if not version_match:
        logger.error(f"No valid version found in title: {full_title}")
        return None

    return version_match.group(1)


def build_apkmirror_download_chain(base_url, version_slug, headers):
    """Build APK download link chain (from main page to final download page)"""
    try:
        download_page_url = f"{base_url.rstrip('/')}/{version_slug}-release/{version_slug}-android-apk-download/"

        download_soup = fetch_page(download_page_url, headers)
        if not download_soup:
            logger.error(f"Download page does not exist or is not accessible: {download_page_url}")
            return None, None

        apk_button = download_soup.find('a', class_='downloadButton', href=True)
        if not apk_button or not apk_button['href']:
            logger.error("Android APK download button not found, page structure may have changed")
            return None, None

        full_apk_url = f"https://www.apkmirror.com{apk_button['href'].rstrip('/')}"
        return download_page_url, full_apk_url

    except Exception as e:
        logger.error(f"Exception occurred while building download chain: {str(e)}")
        return None, None


def get_final_download_url(session, url):
    """Get download link"""
    try:
        response = session.get(url, allow_redirects=True, timeout=15)
        response.raise_for_status()

        if 'r2.cloudflarestorage.com' in response.url:
            return response.url

        soup = BeautifulSoup(response.text, 'html.parser')
        download_link = soup.find('a', id='download-link', href=True)
        if download_link:
            return f"https://www.apkmirror.com{download_link['href']}"

        logger.error("Unable to obtain valid download link, page structure may have changed")
        return None

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to obtain final link: {str(e)}")
        return None


def download_file(session, url, save_path):
    try:
        with session.get(url, stream=True, timeout=20) as response:
            response.raise_for_status()

            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0

            progress_bar = tqdm(
                total=total_size,
                unit='B',
                unit_scale=True,
                unit_divisor=1024,
                desc=os.path.basename(save_path),
                leave=True
            )

            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        progress_bar.update(len(chunk))

            progress_bar.close()
            logger.info(f"File download completed: {save_path} ({downloaded}/{total_size} bytes)" if total_size else
                        f"File download completed: {save_path} (unknown size)")
            return True

    except requests.exceptions.RequestException as e:
        logger.error(f"Download failed: {str(e)} - URL: {url}, Target path: {save_path}")
        return False
    except Exception as e:
        logger.error(f"An unknown error occurred during download: {str(e)} - URL: {url}, Target path: {save_path}")
        return False


def download_apk_editor_jar(file_dir, filename):
    """Download APKEditor.jar"""
    file_path = os.path.join(file_dir, filename)
    if os.path.exists(filename):
        logger.info(f"{filename} already exists, skipping download.")
        return
    try:
        logger.info(f"{filename} not found, starting download...")
        # Construct GitHub API URL
        api_url = f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/releases/latest"
        response = requests.get(api_url)
        response.raise_for_status()
        release_data = response.json()
        assets = release_data.get('assets', [])
        if not assets:
            raise Exception("No available assets found in latest repository release.")
        download_url = assets[0].get('browser_download_url')
        if not download_url:
            raise Exception("Asset download link not found.")
        with requests.Session() as session:
            session.headers.update(HEADERS)
            logger.info(f"Starting download of {filename}: {download_url}")
            if not download_file(session, download_url, file_path):
                raise Exception(f"{filename} download failed.")
            logger.info(f"{filename} download completed, saved to: {file_path}")
    except Exception as e:
        logger.error(f"Error downloading {filename}: {str(e)}")


def download_termius_apk(file_dir, filename):
    """Download Termius.apk"""
    file_path = os.path.join(file_dir, filename)
    if os.path.exists(file_path):
        logger.info(f"{filename} already exists, skipping download.")
        return
    try:
        logger.info(f"{filename} does not exist, starting download...")
        logger.info(f"Fetching version number for {filename}...")
        main_page_soup = fetch_page(BASE_APK_URL, HEADERS)
        if not main_page_soup:
            raise Exception("Failed to access the main page, terminating the program.")
        latest_version = extract_version_from_title(main_page_soup)
        if not latest_version:
            raise Exception("Failed to extract version number, terminating the program.")
        logger.info(f"Detected latest version: {latest_version}")
        version_replace = latest_version.replace('.', '-')
        version_slug = f"termius-modern-ssh-client-{version_replace}"
        _, apk_download_page_url = build_apkmirror_download_chain(BASE_APK_URL, version_slug, HEADERS)
        if not apk_download_page_url:
            raise Exception("Failed to construct a valid download link, terminating the program.")
        with requests.Session() as session:
            session.headers.update(HEADERS)
            direct_download_url = get_final_download_url(session, apk_download_page_url)
            if not direct_download_url:
                raise Exception("Failed to obtain the final download link, terminating the program.")
            logger.info(f"Final download link obtained: {direct_download_url}")
            logger.info(f"Starting download of {filename}...")
            if not download_file(session, direct_download_url, file_path):
                raise Exception(f"{filename} download failed.")
            logger.info(f"{filename} download completed.")
    except Exception as e:
        logger.error(f"Error occurred while downloading {filename}: {str(e)}")


def load_sign_properties(file_dir):
    path_sign_config_file = os.path.join(file_dir, APK_SIGN_PROPERTIES)
    if not os.path.exists(path_sign_config_file):
        path_sign_config_file = os.path.abspath(os.path.join(os.path.expanduser('~'), APK_SIGN_PROPERTIES))
        if not os.path.exists(path_sign_config_file):
            return None

    sign_config_file_lines = list()
    with open(path_sign_config_file, 'r', encoding='UTF-8') as sign_config_file:
        sign_config_file_lines = sign_config_file.readlines()

    properties = dict()
    for line in sign_config_file_lines:
        checked_line = line.strip().replace('\r', '').replace('\n', '')
        if checked_line is None or checked_line == '' or line.startswith('#'):
            continue
        line_parts = checked_line.split('=')
        if len(line_parts) != 2:
            continue
        property_key = line_parts[0].strip()
        property_value = line_parts[1].strip()
        properties[property_key] = property_value

    if 'sign.keystore' not in properties.keys() or 'sign.keystore.password' not in properties.keys() or 'sign.key.alias' not in properties.keys() or 'sign.key.password' not in properties.keys():
        return None
    if properties['sign.keystore.password'] == '' or properties['sign.key.alias'] == '' or properties[
        'sign.key.password'] == '':
        return None
    return properties


def zipalign_apk(file_dir, apk_filename):
    logger.info('Executing zipalign operation on APK')
    built_apk_file = os.path.join(file_dir, apk_filename + EXT_APK)
    if not os.path.exists(built_apk_file):
        raise Exception("APK file for zipalign does not exist")
    built_apk_aligned_file = os.path.join(file_dir, apk_filename + ALIGNED_SUFFIX + EXT_APK)
    if os.path.exists(built_apk_aligned_file):
        os.remove(built_apk_aligned_file)
    run_command(f'zipalign -p -f 4 {built_apk_file} {built_apk_aligned_file}', shell=True)
    os.remove(built_apk_file)
    shutil.move(str(built_apk_aligned_file), str(built_apk_file))
    logger.info('Zipalign operation completed successfully')


def generate_keystore(file_dir, sign_config):
    logger.info('Generating keystore')
    run_command(f'keytool -genkeypair \
    -alias {sign_config["sign.key.alias"]} \
    -keyalg RSA \
    -keysize 2048 \
    -validity 10000 \
    -keystore {os.path.join(file_dir, sign_config["sign.keystore"])} \
    -storepass {sign_config["sign.keystore.password"]} \
    -keypass {sign_config["sign.key.password"]} \
    -dname "CN={sign_config["sign.key.dname.cn"]},C={sign_config["sign.key.dname.c"]}"', shell=True, log=False)
    logger.info('Keystore generation completed')


def sign_apk(file_dir, tmp_dir, apk_filename, sign_properties):
    logger.info('Signing APK file')
    build_apk_file = os.path.join(tmp_dir, apk_filename + EXT_APK)
    if not os.path.exists(build_apk_file):
        raise Exception("APK file for signing does not exist")
    build_apk_signed_file = os.path.join(tmp_dir, apk_filename + SIGNED_SUFFIX + EXT_APK)
    if os.path.exists(build_apk_signed_file):
        os.remove(build_apk_signed_file)
    run_command(f'apksigner sign \
    --ks "{os.path.join(file_dir, sign_properties["sign.keystore"])}" \
    --ks-pass pass:{sign_properties["sign.keystore.password"]} \
    --ks-key-alias {sign_properties["sign.key.alias"]} \
    --key-pass pass:{sign_properties["sign.key.password"]} \
    --out "{build_apk_signed_file}" \
    "{build_apk_file}"', shell=True, log=False)
    os.remove(build_apk_file)
    shutil.move(str(build_apk_signed_file), str(build_apk_file))
    logger.info('APK signing completed')
    logger.info('Verifying APK signature')
    run_command(f'apksigner verify --verbose {build_apk_file}', shell=True)
    logger.info('APK signature verification completed')


def apkm_to_apk(apk_editor_jar, apkm_file, apk_file):
    if not os.path.exists(apk_editor_jar):
        raise Exception(f"{apk_editor_jar} not found.")
    if os.path.exists(apk_file):
        os.remove(apk_file)
    run_command(f'java -jar {apk_editor_jar} m -i {apkm_file} -o {apk_file}', shell=True)


def decode_apk(apk_editor_jar, apk_file, out_dir):
    if not os.path.exists(apk_editor_jar):
        raise Exception(f"{apk_editor_jar} not found.")
    if os.path.exists(out_dir):
        safe_rmtree(out_dir)
    run_command(f'java -jar {apk_editor_jar} d -i {apk_file} -o {out_dir}', shell=True)


def replace_language_xml(source_dir, target_dir):
    if not os.path.exists(source_dir):
        raise Exception(f"Failed to replace {LANGUAGE_XML}, source file not found: {source_dir}")
    src_xml = os.path.join(source_dir, LANGUAGE_XML)
    tar_xml = os.path.join(target_dir, 'resources', 'package_1', 'res', 'values-zh-rCN', LANGUAGE_XML)
    logger.info(f"Replacing language file: Source={src_xml}, Target={tar_xml}")
    replace_file(src_xml, tar_xml)


def build_apk(apk_editor_jar, file_dir, out_dir, apk_filename):
    if not os.path.exists(apk_editor_jar):
        raise Exception(f"{apk_editor_jar} not found.")
    if not os.path.exists(out_dir):
        raise Exception("Decompiled directory not found.")
    apk_file = os.path.join(file_dir, apk_filename + EXT_APK)
    if os.path.exists(apk_file):
        os.remove(apk_file)
    run_command(f'java -jar {apk_editor_jar} b -i {out_dir} -o {apk_file}', shell=True)


def export_apk(file_dir, tmp_dir, apk_filename, export_filename):
    apk_file = os.path.join(tmp_dir, apk_filename + EXT_APK)
    out_dir = os.path.join(file_dir, "out")
    if not os.path.exists(out_dir):
        os.mkdir(out_dir)
    export_apk_file = os.path.join(out_dir, export_filename + EXT_APK)
    if os.path.exists(export_apk_file):
        os.remove(export_apk_file)
    shutil.move(str(apk_file), str(export_apk_file))


def apk_file_modify(file_dir, sign_properties):
    """Modify APK file"""
    tmp_dir = create_tmp_dir(file_dir)
    decompile_dir = os.path.join(tmp_dir, APP_FILE)
    apkm_file = os.path.join(file_dir, APP_FILE + EXT_APKM)
    apk_file = os.path.join(tmp_dir, APP_FILE + EXT_APK)
    filename_zh = APP_FILE + ZH_SUFFIX
    apk_editor_jar = os.path.join(file_dir, APK_EDITOR_FILENAME)

    logger.info("Starting APK file processing")
    apkm_to_apk(apk_editor_jar, apkm_file, apk_file)

    logger.info("Decompiling APK file")
    decode_apk(apk_editor_jar, apk_file, decompile_dir)

    logger.info("Replacing language resources")
    replace_language_xml(file_dir, decompile_dir)

    logger.info("Repackaging APK file")
    build_apk(apk_editor_jar, tmp_dir, decompile_dir, filename_zh)

    logger.info("Executing zipalign operation")
    zipalign_apk(tmp_dir, filename_zh)

    logger.info("Signing APK file")
    sign_apk(file_dir, tmp_dir, filename_zh, sign_properties)

    logger.info("Exporting final APK file")
    export_apk(file_dir, tmp_dir, filename_zh, APP_FILE)

    logger.info(f"Cleaning temporary directory: {tmp_dir}")
    safe_rmtree(tmp_dir)


def file_exists(file_dir, sign_properties):
    language_xml = os.path.join(file_dir, LANGUAGE_XML)
    if not os.path.exists(language_xml):
        raise Exception("Language file not found.")
    termius_apk = os.path.join(file_dir, APKM_FILENAME)
    if not os.path.exists(termius_apk):
        download_termius_apk(file_dir, APKM_FILENAME)
    apk_editor_jar = os.path.join(file_dir, APK_EDITOR_FILENAME)
    if not os.path.exists(apk_editor_jar):
        download_apk_editor_jar(file_dir, APK_EDITOR_FILENAME)
    sign_keystore = os.path.join(file_dir, sign_properties["sign.keystore"])
    if not os.path.exists(sign_keystore):
        generate_keystore(file_dir, sign_properties)


def main():
    logger.info("Process initialization started")
    script_path = Path(__file__).resolve()
    script_dir = script_path.parent
    sign_properties = load_sign_properties(script_dir)
    if not sign_properties:
        logger.error("Signature configuration file not found")
        sys.exit(1)
    try:
        file_exists(script_dir, sign_properties)
        apk_file_modify(script_dir, sign_properties)
    except Exception as e:
        logger.error(f"Process terminated abnormally: {e}")
        sys.exit(1)
    logger.info("Process completed successfully")


if __name__ == "__main__":
    main()
