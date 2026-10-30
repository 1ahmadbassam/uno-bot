import os

# Games created in tests use the OS random generator instead of fetching a drand
# round (fairness.py); the fairness tests turn it on with a fake beacon.
os.environ.setdefault("FAIR_PLAY", "0")
