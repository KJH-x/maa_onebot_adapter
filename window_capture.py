"""
窗口捕获模块 v2.5 (Win32 API 版本)
从 v2.5 layout_design_final.py 提取的完整窗口捕获功能
- 支持 Win32 API (PrintWindow)
- 支持 DPI 感知
- 支持子窗口查找
- 支持明日方舟/MuMu模拟器
"""

from PIL import Image
import logging
from typing import Optional, List, Tuple

# 窗口截图相关导入
try:
    import win32gui
    import win32ui
    from ctypes import windll
    import ctypes
    import win32process
    import psutil
    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False
    logging.warning("win32gui/psutil not available, game capture disabled")


def find_window_by_title(title_keywords: List[str], process_names: Optional[List[str]] = None) -> List[Tuple]:
    """
    根据标题关键词查找窗口
    
    Args:
        title_keywords: 标题关键词列表
        process_names: 进程名列表（可选）
    
    Returns:
        找到的窗口列表 [(hwnd, title, pid, process_name), ...]
    """
    if not WIN32_AVAILABLE:
        return []
    
    found = []
    def callback(hwnd, extra):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            for keyword in title_keywords:
                if keyword in title:
                    if process_names:
                        try:
                            _, pid = win32process.GetWindowThreadProcessId(hwnd)
                            process = psutil.Process(pid)
                            proc_name = process.name().lower()
                            if any(p.lower() in proc_name for p in process_names):
                                found.append((hwnd, title, pid, process.name()))
                                break
                        except:
                            pass
                    else:
                        found.append((hwnd, title, None, None))
                        break
        return True
    win32gui.EnumWindows(callback, None)
    return found


def find_child_windows(parent_hwnd: int) -> List[dict]:
    """
    查找子窗口
    
    Args:
        parent_hwnd: 父窗口句柄
    
    Returns:
        子窗口信息列表
    """
    if not WIN32_AVAILABLE:
        return []
    
    children = []
    def callback(hwnd, extra):
        if win32gui.IsWindowVisible(hwnd):
            rect = win32gui.GetWindowRect(hwnd)
            children.append({
                'hwnd': hwnd,
                'title': win32gui.GetWindowText(hwnd),
                'class': win32gui.GetClassName(hwnd),
                'size': (rect[2] - rect[0], rect[3] - rect[1])
            })
        return True
    win32gui.EnumChildWindows(parent_hwnd, callback, children)
    return children


def capture_window(hwnd: int) -> Optional[Image.Image]:
    """
    捕获指定窗口的截图
    
    Args:
        hwnd: 窗口句柄
    
    Returns:
        PIL Image 对象，失败返回 None
    """
    if not WIN32_AVAILABLE:
        return None
    
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        rect = win32gui.GetWindowRect(hwnd)
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        
        hwndDC = win32gui.GetWindowDC(hwnd)
        mfcDC = win32ui.CreateDCFromHandle(hwndDC)
        saveDC = mfcDC.CreateCompatibleDC()
        saveBitMap = win32ui.CreateBitmap()
        saveBitMap.CreateCompatibleBitmap(mfcDC, w, h)
        saveDC.SelectObject(saveBitMap)
        
        # PrintWindow 参数 2 = PW_RENDERFULLCONTENT
        if windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), 2) == 0:
            win32gui.DeleteObject(saveBitMap.GetHandle())
            saveDC.DeleteDC()
            mfcDC.DeleteDC()
            win32gui.ReleaseDC(hwnd, hwndDC)
            return None
        
        bmpinfo = saveBitMap.GetInfo()
        im = Image.frombuffer(
            'RGB',
            (bmpinfo['bmWidth'], bmpinfo['bmHeight']),
            saveBitMap.GetBitmapBits(True),
            'raw', 'BGRX', 0, 1
        )
        
        # 清理资源
        win32gui.DeleteObject(saveBitMap.GetHandle())
        saveDC.DeleteDC()
        mfcDC.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwndDC)
        
        return im
    except Exception as e:
        logging.warning(f"Window capture failed: {e}")
        return None


def capture_game_window() -> Optional[Image.Image]:
    """
    捕获游戏窗口截图
    
    优先查找明日方舟游戏窗口，备选查找MuMu模拟器
    支持子窗口查找，自动选择最大的游戏画面窗口
    
    Returns:
        PIL Image 对象，失败返回 None
    """
    if not WIN32_AVAILABLE:
        logging.warning("截图功能不可用（缺少win32gui/psutil）")
        return None
    
    logging.debug("查找游戏窗口...")
    
    # 优先查找明日方舟游戏窗口
    windows = find_window_by_title(
        ["明日方舟", "Arknights"],
        process_names=["player", "arknights", "MuMuNxDevice"]
    )
    
    if not windows:
        # 备选查找MuMu模拟器
        windows = find_window_by_title(
            ["MuMu", "模拟器"],
            process_names=["player", "MuMuNxDevice", "MuMuNxMain"]
        )
    
    if not windows:
        logging.debug("未找到游戏窗口")
        return None
    
    # 找到合适的父窗口（排除MAA等工具）
    parent_hwnd = None
    parent_title = None
    parent_pid = None
    
    for hwnd, title, pid, proc_name in windows:
        if "MAA" not in title:
            parent_hwnd = hwnd
            parent_title = title
            parent_pid = pid
            break
    
    if not parent_hwnd:
        parent_hwnd, parent_title, parent_pid, _ = windows[0]
    
    logging.debug(f"找到窗口: {parent_title} (PID: {parent_pid})")
    
    # 查找子窗口
    children = find_child_windows(parent_hwnd)
    
    if not children:
        logging.debug("未找到子窗口，尝试直接截取父窗口")
        img = capture_window(parent_hwnd)
        return img
    
    # 找到游戏画面窗口
    game_window = None
    for c in children:
        if c['class'] == 'nemuwin' or 'nemudisplay' in c['title'].lower():
            game_window = c
            break
    
    if not game_window:
        # 选择最大的窗口
        game_window = max(children, key=lambda x: x['size'][0] * x['size'][1])
    
    logging.debug(f"选择窗口: {game_window['class']} ({game_window['size'][0]}x{game_window['size'][1]})")
    
    img = capture_window(game_window['hwnd'])
    return img


# 保持向后兼容的别名
capture_window_by_title = find_window_by_title


if __name__ == "__main__":
    # 测试
    logging.basicConfig(level=logging.DEBUG)
    
    img = capture_game_window()
    if img:
        print(f"成功捕获窗口: {img.size}")
        img.save("capture_test.png")
        print(f"截图已保存至: capture_test.png")
    else:
        print("未找到游戏窗口或截图失败")
