"""Desktop startup diagnostics; no account credentials or document contents."""
import json
import os
import sys
import traceback
from datetime import datetime, timezone


def stage(name, **details):
    record = {'time': datetime.now(timezone.utc).isoformat(), 'pid': os.getpid(), 'stage': name, **details}
    print('DrawIQ startup ' + json.dumps(record, ensure_ascii=False), flush=True)


def desktop_environment():
    """A Windows desktop launch must use its own Qt Windows plugins."""
    if sys.platform != 'win32':
        return
    removed = [key for key in ('QT_QPA_PLATFORM', 'QT_PLUGIN_PATH',
                               'QT_QPA_PLATFORM_PLUGIN_PATH') if os.environ.pop(key, None) is not None]
    if removed:
        stage('qt.environment.reset', removed=removed)


def failure(name):
    stage(name)
    traceback.print_exc()
    if sys.stderr is not None:
        sys.stderr.flush()
