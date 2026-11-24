# Piezoelectric Gesture Recognition

A Windows desktop application and Python command-line tool for recognizing A-Z wrist-writing gestures from three independent piezoelectric voltage recordings. The channels are palmar (P), radial (R), and dorsal (D). The software supports a single letter, three repeats of the same letter, and a sequence of letters.

## Run

Use Python 3.12 with Tkinter. In the repository root, run:

```shell
python -m pip install -r requirements.txt
python desktop.py
```

## Import reference recordings first

Put one subfolder per letter (`A` through `Z`) under a single parent folder. Each letter must have a labeled file for each channel. All files must have two numeric columns: elapsed time in seconds, then voltage in volts. CSV and XLSX are supported.

```text
references/
  A/A-1.xlsx    # P
  A/A-2.xlsx    # R
  A/A-3.xlsx    # D
  B/B-1.xlsx
  B/B-2.xlsx
  B/B-3.xlsx
  ...
  Z/Z-3.xlsx
```

In the desktop window, select whether each file contains one writing of the letter or three consecutive writings of the same letter. Click **导入 A–Z** and select `references/`, not an individual letter folder. For individual, already segmented trials, filenames such as `A_P_01.csv`, `A_P_02.csv`, `A_R_01.csv`, and `A_D_01.csv` are also supported. The reference bank is built locally for the current session. No data are sent to a server.

## Recognize new recordings

Once all 26 letters and three channels are loaded, select three separate query files in the **待识别信号** section, one each for P, R, and D. For a letter sequence, record the same sequence on each channel in the same order and leave brief pauses between letters. The channels need not be recorded simultaneously. Keep query recordings separate from labeled reference recordings when evaluating accuracy.

## Command line

```shell
python gesture3.py train references --recording-repeats 1 --model local_reference.npz
python gesture3.py spell --model local_reference.npz --p query_p.csv --r query_r.csv --d query_d.csv
```

Use `--recording-repeats 3` for full reference records that contain three writings of the same letter. `train` is the only command that saves a template file; the desktop application keeps its reference bank in memory.

The software segments each channel independently, compares signed waveform shapes with dynamic time warping, and combines evidence across channels in writing order. Match percentages summarize waveform fit, not statistical probabilities. Uncertain positions are marked `?`; incompatible channel segmentations may be rejected rather than forced into a word.

## Files

- `gesture3.py`: input, preprocessing, segmentation, template training, and recognition
- `desktop.py`: desktop interface and waveform display
- `requirements.txt`: Python dependencies

For reproducibility, cite the specific release used in your work rather than the changing default branch.
