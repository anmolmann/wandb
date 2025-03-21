import io
import os
import pathlib
import shutil
import sys
from contextlib import contextmanager
from typing import Any, Dict, Generator, List
from unittest.mock import MagicMock

import filelock
import IPython
import IPython.display
import nbformat
import pytest
import wandb
import wandb.util
from nbclient import NotebookClient
from nbclient.client import CellExecutionError
from wandb.sdk.lib import ipython

if sys.version_info >= (3, 12):
    from typing import override
else:
    from typing_extensions import override


_NOTEBOOK_LOCKFILE = os.path.join(
    os.path.dirname(__file__),
    ".test_notebooks.lock",
)

# wandb.jupyter is lazy loaded, so we need to force it to load
# before we can monkeypatch it
wandb.jupyter.quiet()


@pytest.fixture
def mocked_module(monkeypatch):
    """This allows us to mock modules loaded via wandb.util.get_module."""

    def mock_get_module(module):
        orig_get_module = wandb.util.get_module
        mocked_module = MagicMock()

        def get_module(mod):
            if mod == module:
                return mocked_module
            else:
                return orig_get_module(mod)

        monkeypatch.setattr(wandb.util, "get_module", get_module)
        return mocked_module

    return mock_get_module


@pytest.fixture
def mocked_ipython(monkeypatch):
    monkeypatch.setattr(ipython, "in_jupyter", lambda: True)

    def run_cell(cell):
        print("Running cell: ", cell)  # noqa: T201
        exec(cell)

    mock_get_ipython_result = MagicMock()
    mock_get_ipython_result.run_cell = run_cell
    mock_get_ipython_result.html = MagicMock()

    monkeypatch.setattr(IPython, "get_ipython", lambda: mock_get_ipython_result)
    monkeypatch.setattr(
        IPython.display,
        "display",
        lambda obj, **kwargs: mock_get_ipython_result.html(obj._repr_html_()),
    )

    return mock_get_ipython_result


class WandbNotebookClient(NotebookClient):
    def execute_all(self, store_history: bool = True) -> list:
        executed_cells = []

        for idx, cell in enumerate(self.nb.cells):
            # the first cell is the setup cell
            nb_cell_id = idx + 1
            try:
                # print(cell)
                executed_cell = super().execute_cell(
                    cell=cell,
                    cell_index=idx,
                    store_history=False if idx == 0 else store_history,
                )
            except CellExecutionError as e:
                print("Cell output before exception:")  # noqa: T201
                print("=============================")  # noqa: T201
                for output in cell["outputs"]:
                    if output["output_type"] == "stream":
                        print(output["text"])  # noqa: T201
                raise e
            for output in executed_cell["outputs"]:
                if output["output_type"] == "error" and nb_cell_id != 0:
                    print(f"Error in cell: {nb_cell_id}")  # noqa: T201
                    print("\n".join(output["traceback"]))  # noqa: T201
                    raise ValueError(output["evalue"])
            executed_cells.append(executed_cell)

        return executed_cells

    @property
    def cells(self):
        return iter(self.nb.cells[1:])

    def cell_output(self, cell_index: int) -> List[Dict[str, Any]]:
        """Return a cell's outputs."""
        idx = cell_index + 1
        outputs = self.nb.cells[idx]["outputs"]
        return outputs

    def cell_output_html(self, cell_index: int) -> str:
        """Return a cell's HTML outputs concatenated into a string."""
        idx = cell_index + 1
        html = io.StringIO()
        for output in self.nb.cells[idx]["outputs"]:
            if output["output_type"] == "display_data":
                html.write(output["data"]["text/html"])
        return html.getvalue()

    def cell_output_text(self, cell_index: int) -> str:
        """Return a cell's text outputs concatenated into a string."""
        idx = cell_index + 1
        text = io.StringIO()
        # print(len(self.nb.cells), idx)
        for output in self.nb.cells[idx]["outputs"]:
            if output["output_type"] == "stream":
                text.write(output["text"])
        return text.getvalue()

    def all_output_text(self) -> str:
        text = io.StringIO()
        for i in range(len(self.nb["cells"]) - 1):
            text.write(self.cell_output_text(i))
        return text.getvalue()

    @override
    @contextmanager
    def setup_kernel(self, **kwargs: Any) -> Generator[None, None, None]:
        # Work around https://github.com/jupyter/jupyter_client/issues/487
        # by preventing multiple processes from starting up a Jupyter kernel
        # at the same time.
        open_client_lock = filelock.FileLock(_NOTEBOOK_LOCKFILE)
        open_client_lock.acquire()
        unlocked = False

        try:
            with super().setup_kernel(**kwargs):
                open_client_lock.release()
                unlocked = True
                yield
        finally:
            if not unlocked:
                open_client_lock.release()


@pytest.fixture
def run_id() -> str:
    """Fixture to return a fixed run id for testing."""
    return "lovely-dawn-32"


@pytest.fixture
def notebook(user, run_id, assets_path):
    """Fixture to load a notebook and work with it.

    This launches a live server,
    configures a notebook to use it, and enables
    devs to execute arbitrary cells.
    See test_notebooks.py for usage.
    """
    # wandb-related env vars:
    wandb_env = {k: v for k, v in os.environ.items() if k.startswith("WANDB")}

    # lovely-dawn-32 is run id we use for testing
    wandb_env["WANDB_RUN_ID"] = run_id

    @contextmanager
    def notebook_loader(
        nb_name: str,
        kernel_name: str = "wandb_python",
        notebook_type: ipython.PythonType = "jupyter",
        save_code: bool = True,
        skip_api_key_env: bool = False,
        **kwargs: Any,
    ):
        """Sets up a copy of the provided notebook name in a temporary directory.

        As part of the setup process all environment variables starting with 'WANDB',
        are copied into the notebook as an initial setup cell.

        Args:
            nb_name: The name of the notebook to load from the assets directory.
            kernel_name: The name given to the kernel used to run the notebook.
            notebook_type: The type of notebook to use, from the ipython.PythonType list.
            save_code: Whether to set the WANDB_SAVE_CODE environment variable in the setup cell.
            skip_api_key_env: Whether to delete the WANDB_API_KEY environment variable in the setup cell.
            **kwargs: Additional keyword arguments.

        Returns:
            A context manager yielding a WandbNotebookClient object. Which can be used to execute
            the notebook cells and retrieve the output.
        """
        nb_path = assets_path(pathlib.Path("notebooks") / nb_name)
        shutil.copy(nb_path, os.path.join(os.getcwd(), os.path.basename(nb_path)))
        with open(nb_path) as f:
            nb = nbformat.read(f, as_version=4)

        # set up extra env vars and do monkey-patching.
        # in particular, we import and patch wandb.
        # this goes in the first cell of the notebook.

        setup_cell = io.StringIO()

        # env vars, particularly to point the sdk at the live test server (local_testcontainer):
        if not save_code:
            wandb_env["WANDB_SAVE_CODE"] = "false"
            wandb_env["WANDB_NOTEBOOK_NAME"] = ""
        else:
            wandb_env["WANDB_SAVE_CODE"] = "true"
            wandb_env["WANDB_NOTEBOOK_NAME"] = nb_name
        setup_cell.write("import os\n")
        for k, v in wandb_env.items():
            setup_cell.write(f"os.environ['{k}'] = '{v}'\n")
        # make wandb think we're in a specific type of notebook:
        setup_cell.write(
            "import pytest\n"
            "mp = pytest.MonkeyPatch()\n"
            "import wandb\n"
            f"mp.setattr(wandb.sdk.lib.ipython, '_get_python_type', lambda: '{notebook_type}')\n"
        )

        if skip_api_key_env:
            setup_cell.write("mp.delenv('WANDB_API_KEY')\n")

        # inject:
        nb.cells.insert(0, nbformat.v4.new_code_cell(setup_cell.getvalue()))

        client = WandbNotebookClient(nb, kernel_name=kernel_name)
        with client.setup_kernel(**kwargs):
            yield client

    notebook_loader.base_url = wandb_env.get("WANDB_BASE_URL")

    return notebook_loader
