# MatrixTrack

Lightweight desktop utility for visually auditing 5.1 / 5.1.2 multi-channel
audio speaker configurations and preventing channel-mapping bleed.

## Run

```bash
python -m matrixtrack
```

## Dependencies

Python standard library only (`tkinter`, `queue`, `threading`, `wave`).

## Tests

```bash
python -m unittest tests.test_gui_queue -v
```
