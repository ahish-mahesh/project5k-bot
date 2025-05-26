import sys
import os
from contextlib import contextmanager

@contextmanager
def llama_log_redirect(logfile_path):
    os.makedirs(os.path.dirname(logfile_path), exist_ok=True)
    orig_stdout = sys.stdout
    orig_stderr = sys.stderr
    with open(logfile_path, "a") as f:
        sys.stdout = f
        sys.stderr = f
        try:
            yield
        finally:
            sys.stdout = orig_stdout
            sys.stderr = orig_stderr
