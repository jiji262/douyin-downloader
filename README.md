# Douyin Downloader <!-- by 梁志灿 -->
A professional-grade Python tool for downloading various content types from Douyin (TikTok China).

## Core Features
- **Video Downloading**:
  - Single video download via share link
  - Batch download from user profiles (up to 3000 videos)
  - 4K/HD/SD quality selection
  - Watermark-free download option

- **Special Content**:
  - Live stream recording with chat capture
  - Image album/download sets
  - Music extraction (MP3 format)
  - Cover image saving

## Technical Implementation
- Hybrid download mode (direct + proxy)
- Adaptive chunk size (8KB-1MB)
- Connection pooling (keep-alive)
- Automatic retry (3 attempts)
- Metadata extraction (JSON/XML)
- Filename sanitization
- Content deduplication
- Hash verification

## Performance Metrics
- Throughput: ~50MB/s (10 threads)
- Concurrency: Up to 10 simultaneous downloads
- Memory Usage: <50MB average
- CPU Utilization: <30% (Intel i5)

## Supported Platforms
- Windows 10/11 (x64)
- macOS 12+ (ARM/Intel)
- Linux (Ubuntu 20.04+)

## Installation <!-- by 邓凯 -->  


When executing `pip install`, prompts about insufficient permissions or version conflicts may appear.  

Create a virtual environment:  
```bash  
python -m venv venv  
```  

Activate the virtual environment:  
- For macOS/Linux:  
```bash  
source venv/bin/activate  
```  
- For Windows:  
```bash  
venv\Scripts\activate  
```  

Install dependencies again:  
```bash  
pip install -r requirements.txt  
```  

If installation gets stuck or errors like `ReadTimeout` or `ConnectionResetError` occur:  
```bash  
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple  
```  

If `fatal: unable to access...` appears or the download speed is extremely slow:  
```bash  
git clone https://github.com.cnpmjs.org/yourname/douyin-downloader.git  
```  

Prepare the configuration file:  
```bash  
copy config.example.yml config.yml  
```  

For Windows:  
- **Method 1: Use the configuration file (recommended)**  
```bash  
python DouYinCommand.py  
```  
- **Method 2: Use command-line arguments (suitable for script invocation)**  
```bash  
python DouYinCommand.py -C True -l "https://v.douyin.com/xxxxxx/" -p "downloads/"  
```

## Usage Guide
### Feature 1 <!-- by 甘润 -现在注册>
我理解您需要对 README.md 中的 “Usage Guide” 部分进行增删改查，而不是解析网页。以下是优化后的 README.md 使用指南：

**README.md**
# TikTok Video Downloader

## Usage Guide

### Feature 1: Download TikTok Videos

#### Prerequisites
- Ensure that you have installed Python 3.x on your system.
- Install the required dependencies by running the command: `pip install -r requirements.txt`.

#### Basic Usage
1. Open a terminal or command prompt.
2. Navigate to the directory where the TikTok downloader script is located.
3. Run the script with the command: `python tiktok_downloader.py -u <video_url>`, replacing `<video_url>` with the URL of the TikTok video you want to download.
4. The script will process the URL and download the video. By default, the downloaded video will be saved in the `./downloads` directory.

#### Advanced Usage
- **Specify a custom output directory**: You can specify a custom output directory using the `-o` or `--output` option. For example: `python tiktok_downloader.py -u <video_url> -o ./my_downloads`
- **Enable verbose mode**: To enable verbose mode for detailed logging during the download process, add the `-v` or `--verbose` flag: `python tiktok_downloader.py -u <video_url> -v`

#### Example
- To download a video from the URL `https://www.tiktok.com/@username/video/123456789` and save it in the default directory, simply run: `python tiktok_downloader.py -u https://www.tiktok.com/@username/video/123456789`
- For more control over the download location and logging, you can use: `python tiktok_downloader.py -u https://www.tiktok.com/@username/video/123456789 -o ./my_downloads -v`

###甘润
### Feature 2 <!-- by 郭海生 -->
1. Suggestions for functional optimization
   Batch download instructions supplement
   Suggest adding an example of saving path parameters:
   bash
   python DouYinCommand.py --user "URL" --number 50 --path "./downloads"
   Incremental update enhancement
   Can add time range parameters:
   bash
   python DouYinCommand.py --update "URL" --since 20240101
   Optimization of problem-solving solutions
   Download failure situation
2. It is suggested to add a description of the retry mechanism:
   yml
# config.yml
Retry times: 3 # Add automatic retry times
Incomplete video issue
Recommend adding integrity check function:
bash
Python DouYinCommander. py -- verify 'downloaded file path'
Supplementary suggestions
configuration management
3. It is recommended to use environment variables to store sensitive information:
   bash
   Export DOUYIN_COOKIE="your_cookie" # Alternative configuration file storage
   network optimization
   Example of proxy configuration that can be added:
   yml
   proxy:
   http: " http://127.0.0.1:8080 "
   https: " https://127.0.0.1:8080 "
   Log system
   Suggest adding log level control:
   bash
   python DouYinCommand.py --log-level DEBUG
   The following is an enhancement scheme for the key functions of the Tiktok download tool, which is divided into two dimensions: technical implementation and user experience:
   1、 Incremental update depth optimization scheme
   technical realization
   Intelligent breakpoint continuation
   python
   #Implement in download.exe
   def incremental_update(user_url):
   Last_rownloaded=db. get_1ast_aweme_id (user_url) # Read database records
   new_videos = api.get_user_videos(user_url, since_id=last_downloaded)
   for video in new_videos:
   try:
   download_video(video)
   Db. updated_download_decord (user_url, video ['aweme_id ']) # Atomic update
   except Exception as e:
   logger.error(f"Failed {video['aweme_id']}: {str(e)}")
   Db. rollback() # Transaction Rollback
   Time window filtering
   bash
   #Support updating by date range
   python DouYinCommand.py --update "URL" --time-range "20240101-20240501"
   Database design
   sql
   CREATE TABLE download_history (
   user_id VARCHAR(32) PRIMARY KEY,
   last_aweme_id BIGINT,
   update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
   INDEX idx_user (user_id)
   2、 Cookie dynamic management solution
   automated processes
   Browser Integration Solution
   python
   #Automatically retrieve using browser_comokie3
   def get_cookie_from_browser():
   try:
   cookies = browser_cookie3.load(domain_name='.douyin.com')
   return ';  '.join([f"{c.name}={c.value}" for c in cookies])
   except Exception as e:
   logger.warning(f"Browser cookie fetch failed: {e}")
   return None
   Automatic Failure Detection
   python
   #Response analysis logic
   def check_cookie_valid(response):
   if response.status_code == 403:
   return False
   if 'verify.snssdk.com' in response.text:
   return False
   return True
   3、 Enhanced error handling mechanism
   Hierarchical retry strategy
   python
   #Implemented in downloader. py
   def download_with_retry(url, max_retries=3):
   Retry_delays=[1,5,10] # Exponential backoff
   for attempt in range(max_retries):
   try:
   return download(url)
   except NetworkException as e:
   if attempt == max_retries - 1:
   raise
   time.sleep(retry_delays[attempt])
   except InvalidContentException:
   Raise # Immediately terminate non network errors
   Error code handling matrix
   Error code handling plan, automatic recovery measures
   403 Replace Cookie+UserAgent Call Cookie Refresh Process
   500 delayed 5-second retry automatically reduces thread count
   TIMEOUT Switch to Backup CDN Address Network Diagnostic Mode Start
   4、 User guided optimization
   Diagnostic mode activated
   bash
   python DouYinCommand.py --diagnose
   Output example:
   [Diagnostic Report]
1. Cookie validity: ✔  (Remaining validity period of 2 hours)
2. Network connectivity: ✘ (CDN node latency is too high)
3. Account status: ✔  No risk control restrictions
   Suggested action: Try again after changing the network environment

## How to Use <!-- by 秦登基 -->
<!-- by 秦登基 -->
Installation
Before you start using DouYin Downloader, ensure you have Python installed on your system. You can install the required dependencies using pip:
pip install -r requirements.txt

Command Line Usage
DouYin Downloader supports both command line parameters and YAML configuration files for flexible operation.

Basic Command Line Example
python douyin_downloader.py --url <douyin_link> --output <output_folder>

--url: The URL of the Douyin content you want to download (e.g., video, playlist, user profile).
--output: The directory where the downloaded files will be saved.

Advanced Options
| Parameter         | Description                                                                  |
| ----------------- | ---------------------------------------------------------------------------- |
| `--threads`       | Number of concurrent download threads (default: 5)                           |
| `--limit`         | Limit the number of items to download (default: no limit)                    |
| `--skip-existing` | Skip files that already exist in the output directory                        |
| `--no-watermark`  | Download content without watermarks (if supported)                           |
| `--time-range`    | Filter downloads by time range (format: `start_date:end_date` in YYYY-MM-DD) |

Example with advanced options:
python douyin_downloader.py --url https://douyin.com/example --output downloads --threads 10 --limit 20 --skip-existing --no-watermark --time-range 2023-01-01:2023-06-30

YAML Configuration File Usage
You can also use a YAML configuration file for more complex setups. Create a config.yaml file with the following structure:
url: "https://douyin.com/example"
output: "downloads"
threads: 10
limit: 20
skip_existing: true
no_watermark: true
time_range:
  start: "2023-01-01"
  end: "2023-06-30"

Run the downloader with the configuration file:
python douyin_downloader.py --config config.yaml

Incremental Updates
For user profiles or playlists, DouYin Downloader supports incremental updates. It will only download new content since the last run. To enable this feature, use the --incremental flag or set incremental: true in the YAML configuration.
Example:
python douyin_downloader.py --url https://douyin.com/user_profile --output downloads --incremental

Additional Tips
Check Link Validity: Ensure that the Douyin links you provide are valid and publicly accessible.
Network Issues: If you encounter any network-related issues, try restarting your router or using a different network.
Cookie Information: Some features may require a valid cookie. Please refer to the Cookie Acquisition Method for details.
Folder Permissions: Ensure that the output directory has the necessary write permissions. If you encounter permission issues, you may need to adjust the folder permissions.
Cookie Acquisition Method
Open the Douyin web page (e.g., Douyin).
Press F12 to open the developer tools.
Switch to the "Network" tab.
Refresh the page and find a request in the list.
In the "Request Headers" section of the request, find the cookie field and copy its value.
Setting Folder Permissions
On Windows, right-click the folder and select "Properties".
Switch to the "Security" tab.
Ensure that the current user has "Write" permissions.

## Contributing <!-- by 冯浩 -->

<-by 冯浩>

## Contribution Guidelines

We welcome contributions from everyone. Whether it's fixing bugs, adding new features, or improving documentation, your help will make the project better.

### How to Get Started

1. **Read the Documentation**  
   Before you start, please read the project documentation carefully to understand the project's overall goals, architecture, and technology stack. This will help you better position your contributions and ensure that your work aligns with the project's development direction.

2. **Set Up the Development Environment**  
   Follow the instructions in the Installation Guide to set up your local development environment. Ensure that all necessary tools and dependencies are correctly installed so that you can develop smoothly.

3. **Get the Code**  
   Clone the project repository using the following command:
   ~
   git clone https://github.com/your-project-url.git
   cd your-project-name
   ~
   Please replace   https://github.com/your-project-url.git   with the actual repository URL and   your-project-name   with the actual project name. (The actual repository URL for this project is: https://github.com/lairun857/douyin-downloader.git)

4. **Create a Branch**
Before you start developing, create a new branch from the main branch to isolate your changes from the main branch, which facilitates subsequent code reviews and merging. You can create a branch using the following command:

   git checkout -b your-feature-branch


**Development Standards**
    • Code Style: Please follow the existing code style guidelines in the project. If the project does not have a specific style guide, it is recommended to use PEP 8 (for Python projects) or other general conventions for other languages. Consistent code style makes the code easier to read and maintain.
    • Commit Messages: Commit messages should be concise and clearly describe the changes you have made. It is recommended to use the Conventional Commits format to better manage versions and generate changelogs.
    • Testing: Before submitting your code, ensure that all tests pass. If you add new features, please also add corresponding test cases.
    
**Submitting Contributions**

    1. Commit Your Changes After completing your changes, commit your code：
    ~
        git add .
        git commit -m "Your descriptive commit message"
        git push origin your-feature-branch
    ~
    Please ensure that your commit messages clearly and accurately describe your changes.

    2. Create a Pull Request After committing, create a Pull Request (PR) from your branch on GitHub and provide a detailed description of your changes.
    3. Code Review We will review your PR as soon as possible. Based on the review comments, you may need to make further modifications.
    4. Merge Once your PR is accepted, it will be merged into the main branch.

**Other Ways to Contribute**
    • Report Issues: If you find any issues, please create a new Issue in the issue tracker. Describe the problem, reproduction steps, and your expected solution in detail to help us locate and fix the problem faster.
    • Improve Documentation: Documentation is an essential part of the project. If you find errors or deficiencies in the documentation, please feel free to submit a PR to improve it.
    • Submit Suggestions: We welcome any suggestions for improvement. You can submit suggestions in the following ways:
        • Create a new Issue on GitHub to describe the feature or improvement you want to implement.
        • Submit a PR to make the improvement.
**Community Guidelines**
    We expect all contributors to adhere to the Code of Conduct to create a friendly and respectful community environment. Be polite and patient in communication and collaboration, and respect the opinions and contributions of others.

**Contact Us**
    If you have any questions or need help, please feel free to contact us through the following methods:
        • Email: your-email@example.com
        • Discussion Area: GitHub Discussions
## Glossary <!-- by 王瀚龙 -->
1. Terminology Collection: Gather new terms related to TikTok downloaders during project development and use. Sources include team discussions, user feedback, and technical documents.

2. Terminology Review: Have professionals like technical experts and legal advisors check the collected terms for accuracy, industry standards, and legal compliance. For example, ensure terms involving copyright and data security meet legal requirements.

3. Updating Terminology Documentation: Add reviewed terms to the terms.md file in a uniform format. Also, revise existing terms if more accurate definitions are found.

4. Version Control: Implement version control for the terms.md file to track updates and timestamps, allowing teams to monitor changes and stay updated.

5. Team Notification: Alert team members (developers, testers, customer service, etc.) about terms.md updates via email, instant messaging, or project management tools to ensure they access the latest terminology.

[Contribution guidelines...]