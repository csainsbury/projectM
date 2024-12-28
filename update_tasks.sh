#!/bin/bash

# Set conda path
export PATH="/Users/csainsbury/anaconda3/bin:$PATH"

# Activate conda environment
source /Users/csainsbury/anaconda3/etc/profile.d/conda.sh
conda activate projectM

# Add the directory containing projectM.py to PYTHONPATH
cd /Users/csainsbury/Documents/projectM
export PYTHONPATH="${PWD}:${PYTHONPATH}"

# Run the python script
python createTasks.py 