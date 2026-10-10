# Swap X and Y in a 2D heatmap
> One button turns the heatmap on its side: the old X axis becomes Y and the other way round. New in 2.0.

## Shortest procedure
1. **Switch to 2D.** Set **Plot Mode** to **2D Heatmap** and choose Z, X and Y in **PLOT SETTINGS**. Find **Swap X / Y** under the Y box. ![](new_swap_button.png)
2. **Press Swap X / Y.** The X and Y boxes exchange their channels and the plot is drawn transposed; colours (Z) are unchanged. ![](new_swap_result.png)

## What the result means
- Nothing is recalculated: the same data are shown with the axes exchanged. Press the button again to go back.
- Marks that sit at an X or Y position are tied to the axes, so check them after a swap.

## Options and parameters
- The Transform, Colormap and colour range are kept. Only 2D mode has this button.

## If it does not work
- **The button is grey**: no 2D plot is shown (1D mode, or no file open).
