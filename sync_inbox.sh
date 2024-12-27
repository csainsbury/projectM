#!/bin/bash

# Set conda path
export PATH="/Users/csainsbury/anaconda3/bin:$PATH"

# Activate conda environment
source activate projectM

# Add the directory containing projectM.py to PYTHONPATH
export PYTHONPATH="/Users/csainsbury/projectM:$PYTHONPATH"

# Run the python command - note the changed import path
/Users/csainsbury/anaconda3/envs/projectM/bin/python -c "import sys; sys.path.append('/Users/csainsbury/projectM'); from projectM import sync_local_to_github; sync_local_to_github('inbox')"