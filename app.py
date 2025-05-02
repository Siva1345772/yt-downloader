from flask import Flask, render_template, request, jsonify, send_from_directory, abort
import yt_dlp
import os
import shutil
import tempfile
import logging
import secrets
import re
import time
import threading
from urllib.parse import urlparse, unquote

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', secrets.token_hex(32))  # Secure key with env variable or random fallback

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Dictionary to keep track of downloads
active_downloads = {}

def check_ffmpeg():
    """Check if ffmpeg is installed and return its path."""
    hardcoded_ffmpeg_path = r"C:\Users\sivasakthi\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-7.1.1-full_build\bin\ffmpeg.exe"
    
    if os.path.exists(hardcoded_ffmpeg_path):
        logger.info(f"FFmpeg found at: {hardcoded_ffmpeg_path}")
        return hardcoded_ffmpeg_path
    
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path is None:
        logger.warning("FFmpeg not found in PATH. Falling back to single stream download.")
        return None
    
    logger.info(f"FFmpeg found at: {ffmpeg_path}")
    return ffmpeg_path

def is_valid_youtube_url(url):
    """Check if the URL is a valid YouTube URL."""
    parsed = urlparse(url)
    if not parsed.scheme in ('http', 'https'):
        return False
    if not any(domain in parsed.netloc for domain in ('youtube.com', 'youtu.be')):
        return False
    return True

def sanitize_filename(filename):
    """Sanitize filename to remove unsafe characters and ensure compatibility."""
    # Remove or replace unsafe characters
    filename = re.sub(r'[^\w\s-]', '', filename)  # Keep alphanumeric, spaces, hyphens
    filename = re.sub(r'\s+', '_', filename.strip())  # Replace spaces with underscores
    return filename

def cleanup_temp_dirs():
    """Periodically clean up temporary directories older than 5 minutes."""
    while True:
        current_time = time.time()
        for filename, temp_dir in list(active_downloads.items()):
            if os.path.exists(temp_dir):
                dir_mtime = os.path.getmtime(temp_dir)
                if current_time - dir_mtime > 300:  # 5 minutes
                    try:
                        shutil.rmtree(temp_dir, ignore_errors=True)
                        active_downloads.pop(filename, None)
                        logger.info(f"Cleaned up expired temp directory: {temp_dir}")
                    except Exception as e:
                        logger.error(f"Cleanup error for {temp_dir}: {str(e)}")
        time.sleep(60)  # Check every minute

def download_video(url, preferred_resolution=None):
    """Download YouTube video with the highest available quality."""
    try:
        temp_dir = tempfile.mkdtemp()
        logger.info(f"Created temp directory: {temp_dir}")
        
        ffmpeg_path = check_ffmpeg()
        ffmpeg_available = ffmpeg_path is not None
        
        if ffmpeg_available:
            if preferred_resolution:
                format_string = f'bestvideo[ext=mp4][height<={preferred_resolution}]+bestaudio[ext=m4a]/best[ext=mp4][height<={preferred_resolution}]'
            else:
                format_string = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
        else:
            format_string = 'best[ext=mp4]'
        
        ydl_opts = {
            'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
            'format': format_string,
            'merge_output_format': 'mp4',
            'quiet': True,
            'no_warnings': True,
        }
        
        if ffmpeg_available:
            ydl_opts['ffmpeg_location'] = ffmpeg_path
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            original_filename = ydl.prepare_filename(info)
            actual_path = os.path.join(temp_dir, os.path.basename(original_filename))
            
            if not os.path.exists(actual_path):
                base_name = os.path.splitext(os.path.basename(original_filename))[0]
                for f in os.listdir(temp_dir):
                    if f.startswith(base_name):
                        actual_path = os.path.join(temp_dir, f)
                        break
            
            if not os.path.exists(actual_path):
                raise FileNotFoundError(f"Downloaded file not found in temp directory: {temp_dir}")
            
            title = info.get('title', 'video')
            # Sanitize the filename for the download URL
            sanitized_filename = sanitize_filename(title) + '.mp4'
            duration = info.get('duration', 0)
            resolution = info.get('height', 'unknown')
            
            logger.info(f"Downloaded: {title} (Sanitized: {sanitized_filename}, Duration: {duration}s, Resolution: {resolution}p)")
        
        return {
            'success': True,
            'filename': sanitized_filename,
            'original_path': actual_path,
            'title': title,
            'temp_dir': temp_dir
        }
        
    except yt_dlp.utils.DownloadError as e:
        logger.error(f"Download error: {str(e)}")
        return {'success': False, 'error': str(e)}
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return {'success': False, 'error': f"An unexpected error occurred: {str(e)}"}

@app.route('/')
def index():
    return render_template('demo.html')

@app.route('/developer')
def developer():
    return render_template('developer.html')

@app.route('/download', methods=['POST'])
def handle_download():
    data = request.get_json()
    url = data.get('url', '').strip()
    
    if not url:
        return jsonify({'success': False, 'error': 'URL is required'}), 400
    
    if not is_valid_youtube_url(url):
        return jsonify({'success': False, 'error': 'Please enter a valid YouTube URL'}), 400
    
    result = download_video(url)
    
    if result['success']:
        active_downloads[result['filename']] = result['temp_dir']
        return jsonify({
            'success': True,
            'filename': result['filename'],
            'title': result['title'],
            'download_url': f"/download/{result['filename']}"
        })
    else:
        return jsonify({'success': False, 'error': result['error']}), 500

@app.route('/download/<filename>')
def serve_download(filename):
    # Decode URL-encoded filename
    filename = unquote(filename)
    logger.info(f"Attempting to serve file: {filename}")
    
    if '..' in filename or filename.startswith('/'):
        logger.error("Invalid filename detected")
        abort(400, "Invalid filename")
    
    temp_dir = active_downloads.get(filename)
    if not temp_dir or not os.path.exists(temp_dir):
        logger.error(f"Temp directory not found for filename: {filename}")
        abort(404, "File not found or download expired")
    
    # Try to find the actual file
    for f in os.listdir(temp_dir):
        if sanitize_filename(os.path.splitext(f)[0]) == sanitize_filename(os.path.splitext(filename)[0]):
            file_path = os.path.join(temp_dir, f)
            break
    else:
        logger.error(f"No matching file found in temp directory: {temp_dir}")
        abort(404, "File not found")
    
    if os.path.exists(file_path):
        logger.info(f"Serving file: {file_path}")
        response = send_from_directory(
            temp_dir,
            os.path.basename(file_path),
            as_attachment=True,
            download_name=filename
        )
        return response
    else:
        logger.error(f"File does not exist: {file_path}")
        abort(404, "File not found")

# Start cleanup thread
cleanup_thread = threading.Thread(target=cleanup_temp_dirs, daemon=True)
cleanup_thread.start()

if __name__ == '__main__':
    app.run(debug=True, port=5000)