"""v1.0.10: Lab Control .nc → Labber data logs inside a Labber *database* (Database.hdf5 + 年/月/Data_MMDD).

Merged from the standalone "Labber Converter" 0.4.2 (converter.py, labber_format.py, safe_io.py are kept as
they were, so its behaviour and tests carry over). The GUI is app/gui/labber_db_dialog.py.

    convert_batch(inputs, root, ...)   convert files / folders / ZIPs into the database at ``root``
    initialize_database(root)          create (or check) Database.hdf5
    reindex(root, rebuild=False)       sync the Labels of Database.hdf5 with the files in ``root``
"""
from .converter import (VERSION, ConversionError, convert_batch, discover, initialize_database,  # noqa: F401
                        merge_database, reindex, verify_database)
