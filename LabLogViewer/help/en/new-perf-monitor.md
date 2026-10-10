# Performance Monitor
> A live window that shows whether LabLogViewer is lagging, how much CPU and memory it uses, and which function the interface was busy in. New in 2.0.

## Shortest procedure
1. **Open it.** **Settings → Appearance → Advanced → Performance Monitor...** ![](new_perf_button.png)
2. **Watch the three curves.** *UI lag (ms)* is how late the interface answers (under about 50 ms feels smooth), *CPU* is percent of one core, *Memory* is the program's memory in MB. Use the program normally (open data, switch traces) while the window stays open. ![](new_perf_window.png)
3. **Find what was slow.** *Where the UI thread spends its time* ranks the functions by share of time; *Recent stalls* lists every freeze of 0.15 s or longer with its time, duration and where it was stuck. ![](new_perf_stalls.png)
4. **Keep a record.** **Copy Report** or **Save Report...** (saved to the data folder's `logs/performance-<time>.txt`) to send to the developer. **Pause** freezes the curves, **Reset** starts again.

## What the result means
- Lag that stays near 0 and no stalls: the interface is fine; a slow action is then work done in the background.
- Memory that rises every time you open and close the same file, and never levels off, means something is not released; send the report.
- A function that takes most of the time during a stall is where to look; the monitor only observes and changes nothing.

## Options and parameters
- The monitor samples about 25 times a second; the effect on speed is negligible. Closing the window stops the display.

## If it does not work
- **The window is empty at first**: curves need a few seconds to fill.
- **Memory shows 0**: the operating system did not give the value; the other curves still work.
