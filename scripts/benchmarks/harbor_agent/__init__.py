"""OpenProgram as a Harbor agent, for Harbor datasets such as FrontierHarness Eval.

``agent.py`` is the Harbor class (it imports Harbor); ``commands.py`` holds
the container commands and the usage parsing (it does not). This package
imports neither, so loading ``commands`` never needs Harbor installed.
"""
