"""Useful exception metadata without messages, locals, URLs or private paths."""
from collections import deque
from pathlib import Path
import re

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parent.parent
ROOT_MODULES = frozenset(('capture.py', 'count_eggs.py', 'data_layout.py',
                          'recognize_chickens.py', 'sort_captures.py', 'box_split.py'))
CV_FUNCTIONS = frozenset(('remap', 'resize', 'cvtColor', 'GaussianBlur', 'blur', 'Canny',
                         'findContours', 'drawContours', 'erode', 'dilate', 'morphologyEx',
                         'distanceTransform', 'imread', 'imdecode', 'imwrite', 'imencode'))
CV_REASONS = {-215: 'assertion_failed', -5: 'bad_argument', -201: 'incorrect_size',
              -209: 'unmatched_sizes', -210: 'unsupported_format'}
ERROR_TYPES = (PermissionError, FileNotFoundError, IsADirectoryError, NotADirectoryError,
               MemoryError, TimeoutError, OSError, TypeError, ValueError, KeyError,
               IndexError, AttributeError, ImportError, RuntimeError, AssertionError,
               ArithmeticError)


def exception_details(error):
    """Show cause types and public application locations, never raw exceptions.

    Exception text, traceback source lines and locals can contain configuration,
    credential-bearing URLs or private reference filenames. Do not format them,
    even at debug level. Library function names/codes are strictly allowlisted.
    """
    causes = []
    visited = set()
    while error is not None and id(error) not in visited and len(causes) < 4:
        visited.add(id(error))
        category = ('cv2.error' if isinstance(error, cv2.error) else
                    next((kind.__name__ for kind in ERROR_TYPES if isinstance(error, kind)), 'Exception'))
        if isinstance(error, cv2.error):
            code = getattr(error, 'code', None)
            function = getattr(error, 'func', None)
            if type(code) is int and -10000 <= code <= 0:
                category += ' code={}'.format(code)
                if code in CV_REASONS:
                    category += ' ' + CV_REASONS[code]
            if isinstance(function, str) and function in CV_FUNCTIONS:
                category += ' function=' + function
        locations = deque(maxlen=8)
        trace = error.__traceback__
        while trace is not None:
            try:
                relative = Path(trace.tb_frame.f_code.co_filename).relative_to(ROOT)
            except ValueError:
                relative = None
            if relative is not None:
                parts = relative.parts
                public = ((len(parts) == 1 and parts[0] in ROOT_MODULES)
                          or (len(parts) == 2 and parts[0] == 'ha_service'
                              and re.fullmatch(r'[a-z_]+\.py', parts[1])))
                if public:
                    locations.append('{}:{}'.format(relative.as_posix(), trace.tb_lineno))
            trace = trace.tb_next
        if locations:
            category += ' at ' + ' -> '.join(locations)
        causes.append(category)
        error = error.__cause__ if error.__cause__ is not None else error.__context__

    def version(value):
        match = re.match(r'\d+(?:\.\d+){0,3}', value)
        return match.group() if match else 'unknown'

    return '{}; NumPy {}; OpenCV {}'.format(' caused by '.join(causes),
                                           version(np.__version__), version(cv2.__version__))
