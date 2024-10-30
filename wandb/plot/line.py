from __future__ import annotations

from typing import TYPE_CHECKING

from wandb.plot.viz import CustomChart

if TYPE_CHECKING:
    import wandb


def line(
    table: wandb.Table,
    x: str,
    y: str,
    stroke: str | None = None,
    title: str = "",
    split_table: bool = False,
) -> CustomChart:
    """Constructs a customizable line chart.

    Args:
        table (wandb.Table):  The table containing data for the chart.
        x (str): Column name for the x-axis values.
        y (str): Column name for the y-axis values.
        stroke (str):Column name to differentiate line strokes (e.g., for
            grouping lines).
        title (str):Title of the chart.
        split_table (bool): Whether to split the table into a separate section.
            Defaults to False.

    Returns:
       CustomChart: A plot object, to be passed to wandb.log()

    Example:
       ```python
        import math
        import random
        import wandb

        # Create multiple series of data with different patterns
        data = []
        for i in range(100):
            # Series 1: Sinusoidal pattern with random noise
            data.append([i, math.sin(i / 10) + random.uniform(-0.1, 0.1), "series_1"])
            # Series 2: Cosine pattern with random noise
            data.append([i, math.cos(i / 10) + random.uniform(-0.1, 0.1), "series_2"])
            # Series 3: Linear increase with random noise
            data.append([i, i / 10 + random.uniform(-0.5, 0.5), "series_3"])

        # Define the columns for the table
        table = wandb.Table(data=data, columns=["step", "value", "series"])

        # Initialize wandb run and log the line chart
        with wandb.init(project="line_chart_example") as run:
            line_chart = wandb.plot.line(
                table=table,
                x="step",
                y="value",
                stroke="series",  # Group by the "series" column
                title="Multi-Series Line Plot",
            )
            run.log({"line-chart": line_chart})
        ```
    """
    return CustomChart(
        id="wandb/line/v0",
        data=table,
        fields={"x": x, "y": y, "stroke": stroke},
        string_fields={"title": title},
        split_table=split_table,
    )
