import yaml
from pathlib import Path
from fastapi import APIRouter, HTTPException
from typing import Dict, Any

router = APIRouter()
CONFIG_PATH = Path(__file__).parent.parent.parent / "config.yml"

@router.get("/")
def get_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise HTTPException(status_code=404, detail="Config file not found")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    
    config_dict = yaml.safe_load(content) or {}
    
    # 从原始文件中提取被注释掉的链接行 (#- https://...)
    import re
    commented = re.findall(r'^#- *(https?://\S+)', content, re.MULTILINE)
    if commented:
        if not isinstance(config_dict.get('link'), list):
            config_dict['link'] = []
        for url in commented:
            config_dict['link'].append("# " + url)

    return config_dict

@router.post("/")
def save_config(config_data: Dict[str, Any]):
    if CONFIG_PATH.exists():
        backup_path = CONFIG_PATH.with_suffix(".yml.bak")
        backup_path.write_text(CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    
    # 分离出被忽略的链接URL（去掉#前缀）
    all_links = config_data.get('link', []) or []
    ignored_urls = set()
    clean_links = []
    for link in all_links:
        if isinstance(link, str) and link.startswith('#'):
            url = link.lstrip('#').strip()
            ignored_urls.add(url)
            clean_links.append(url)  # 先全部当正常链接写入
        else:
            clean_links.append(link)
    
    config_data['link'] = clean_links
    yaml_text = yaml.dump(config_data, allow_unicode=True, sort_keys=False)
    
    # 在输出文本中，对被忽略的链接行首直接加 #
    for url in ignored_urls:
        yaml_text = yaml_text.replace(f"- {url}", f"#- {url}")
    
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(yaml_text)
        
    return {"status": "success", "message": "Configuration saved"}

@router.post("/pick_folder")
def pick_folder():
    import tkinter as tk
    from tkinter import filedialog
    
    # Needs to be run in a way that doesn't block FastAPI usually, 
    # but considering this runs locally for a single user, blocking is fine.
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    
    folder_path = filedialog.askdirectory(title="选择下载保存路径")
    root.destroy()
    
    if folder_path:
        # User selected a path
        return {"path": folder_path.replace('/', '\\')}
    else:
        # User canceled
        return {"path": None}
