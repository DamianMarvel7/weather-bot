"""Launcher — delegates to src/weatherbot/weatherbet.py"""
import runpy, sys, os
sys.path.insert(0, os.path.dirname(__file__))
runpy.run_module("src.weatherbot.weatherbet", run_name="__main__", alter_sys=True)
