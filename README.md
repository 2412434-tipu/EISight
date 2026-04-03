# EISight: Low-Cost EIS for Food Adulteration Detection

## Project Overview
This repository contains the software pipeline, firmware, and hardware schematics for an IoT-enabled Electrical Impedance Spectroscopy (EIS) system. The project aims to detect adulterants in liquid commodities (with an initial focus on milk) using a low-cost AD5933 impedance converter paired with an ESP32 microcontroller.

## Repository Structure
- `/pipeline` - The canonical machine learning preprocessing and inference scripts.
- `/data/simulated` - Synthetic Cole-Cole impedance data.
- `/data/real` - Physical hardware measurements (AD5933).
- `/data/legacy` - Archived scripts and previous iteration data.
- `/figures` - Generated plots (Bode/Nyquist, feature importance).
- `/docs` - Progress reports, literature reviews, and SOPs.
- `/firmware` - C++ code for the ESP32 (I2C communication with AD5933).
- `/hardware` - Fritzing diagrams and cell design.
- `/app` - Smartphone/Dashboard UI files.
- `/paper` - LaTeX source files for documentation.

## Software Stack
The machine learning pipeline is built in Python 3.11 using `scikit-learn`, `pandas`, and `numpy`. 
To replicate the environment, install the requirements via:
`pip install -r pipeline/requirements.txt`