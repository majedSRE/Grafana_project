"""A short-lived Docker log source for collector integration testing."""
import sys
import time

time.sleep(10)
print(sys.argv[1], flush=True)
time.sleep(20)
