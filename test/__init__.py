import os

# Games created in tests use the OS random generator instead of fetching a drand
# round (fairness.py); the fairness tests turn it on with a fake beacon.
os.environ["FAIR_PLAY"] = "0"
try:
    import config
    config.FAIR_PLAY = False
except ImportError:
    pass

