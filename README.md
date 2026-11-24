# Piezoelectric Gesture Recognition

A Windows desktop application and Python command-line tool for recognizing A-Z wrist-writing gestures from three independent piezoelectric voltage recordings. The channels are palmar (P), radial (R), and dorsal (D). The software supports a single letter, three repeats of the same letter, and a sequence of letters.

## Run

Use Python 3.12 with Tkinter. In the repository root, run:

```shell
python -m pip install -r requirements.txt
python desktop.py
```

The desktop application takes three files, one per channel. Each CSV or XLSX contains elapsed time in seconds and voltage in volts, in that order. For a letter sequence, record the same sequence on each channel in the same order and leave brief pauses between letters. The channels need not be recorded simultaneously.

## Try the synthetic example

```shell
python gesture3.py spell --p examples/HELLO/HELLO_P.csv --r examples/HELLO/HELLO_R.csv --d examples/HELLO/HELLO_D.csv
```

The example contains three independently simulated recordings of `HELLO`. `AZ_templates_refined.npz` contains 702 simulated reference waveforms: 26 letters, three channels, and nine templates per letter and channel. Neither the bundled recordings nor the reference waveforms are experimental measurements. Performance on these files is not evidence of real-world recognition accuracy.

## Build templates from new recordings

Organize single-gesture captures in one directory per letter (A through Z). For example, use `trials/A/A_P_01.csv`, `trials/A/A_P_02.csv`, and corresponding `A_R_*` and `A_D_*` files. Provide at least two labeled captures per letter and channel; do not use continuous words or unsegmented repeat recordings as individual templates.

```shell
python gesture3.py train trials --source "Measured templates" --model measured_v1.npz
python desktop.py --model measured_v1.npz
```

The software segments each channel independently, compares signed waveform shapes with dynamic time warping, and combines evidence across channels in writing order. Match percentages summarize waveform fit, not statistical probabilities. Uncertain positions are marked `?`; incompatible channel segmentations may be rejected rather than forced into a word.

## Files

- `gesture3.py`: input, preprocessing, segmentation, template training, and recognition
- `desktop.py`: desktop interface and waveform display
- `requirements.txt`: Python dependencies
- `AZ_templates_refined.npz`: simulated template bank
- `examples/HELLO/`: three simulated example traces

For reproducibility, cite the specific release used in your work rather than the changing default branch.
