import os

# Qt widgets in tests run without a display (CI runner, ssh sessions).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
