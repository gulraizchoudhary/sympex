"""Headless visual widget startup smoke; does not verify real device recording."""
import sys, types
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
p=types.ModuleType('devices.polar')
p.PolarRecorder=type('PolarRecorder', (), {'__init__':lambda self,bus:None})
sys.modules['devices.polar']=p
import tkinter as tk
from app import App
r=tk.Tk()
a=App(r)
r.update_idletasks()
assert len(a.tabs.tabs()) == 8
assert a.tabs.tab(a.tabs.tabs()[0], 'text').strip() == 'Experiment dashboard'
for t in a.tabs.tabs():
    a.tabs.select(t)
    r.update_idletasks()
print('IMMSS GUI startup and 8-tab traversal: PASS')
r.destroy()
a.bus.close()
