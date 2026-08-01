import subprocess, sys, os
r = subprocess.run([sys.executable, "-m", "pytest", "-q", "--no-header", "-p",
                    "no:cacheprovider", os.path.dirname(os.path.abspath(__file__))],
                   capture_output=True, text=True, timeout=300)
print(1.0 if r.returncode == 0 else 0.0)
