import importlib
import os.path
import glob
modules = glob.glob(os.path.join(os.path.dirname(__file__), "*.py"))
__all__ = [os.path.basename(f)[:-3]
           for f in modules if os.path.isfile(f)
           and not f.endswith('__init__.py')]
modules = [importlib.import_module("source.plugins." + name)
           for name in __all__]
entrypoints = [getattr(module, name)
               for name, module in zip(__all__, modules)
               if callable(getattr(module, name))]
