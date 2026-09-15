"""让测试能 import bin/ 下的模块。"""
import os, sys

def bindir():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for p in (os.path.join(root, 'bin'), os.path.dirname(os.path.abspath(__file__))):
        if p not in sys.path:
            sys.path.insert(0, p)
    return root
