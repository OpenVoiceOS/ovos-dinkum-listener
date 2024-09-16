import uuid
import string
from ovos_utils.time import now_local, now_utc


class _TemplateFilenameFormatter:
    """
    Helper to dynamically filename parts based on a user-specified template.

    Each instance of this builder can be customized to support different keys,
    but some common ones are builtin like "uuid4", "now", and "utcnow"

    Example:
        >>> # Simple now and uuid4 keys are available by default.
        >>> template = 'my_filename_{now}_{uuid4}'
        >>> self = _TemplateFilenameFormatter()
        >>> name = self.format(template)
        >>> # xdoctest: +IGNORE_WANT
        >>> print(f'name={name}')
        name=my_filename_2024-09-14 18:53:22.619838-05:00_7fe91270-3266-42c1-89d9-0809b9facb9e

    Example:
        >>> # The now can use standard python format-string semantics
        >>> template = 'my_filename_{now:%Y-%m-%dT%H%M%S%z}_{uuid4}'
        >>> self = _TemplateFilenameFormatter()
        >>> name = self.format(template)
        >>> # xdoctest: +IGNORE_WANT
        >>> print(f'name={name}')
        name=my_filename_2024-09-14T185354-0500_6f0f6daf-cd81-4c5b-bf38-76a4466161c6

    Example:
        >>> # You can define how to handle custom keys
        >>> template = '{mykey}.bar.{now:%Y-%z}-{uuid4}'
        >>> self = _TemplateFilenameFormatter()
        >>> @self.register('mykey')
        >>> def custom_func():
        ...     return 'myval'
        >>> name = self.format(template)
        >>> # xdoctest: +IGNORE_WANT
        >>> print(f'name={name}')
        name=myval.bar.2024--765176fa-7c80-431c-b43d-2ad14a58a249

    Example:
        >>> # should raise an error if template contains an unknown field
        >>> template = '{doesnotexist}.bar.{now:%Y-%z}-{uuid4}'
        >>> self = _TemplateFilenameFormatter()
        >>> import pytest
        >>> with pytest.raises(KeyError) as ex:
        ...     name = self.format(template)
        >>> # xdoctest: +IGNORE_WANT
        >>> print(str(ex.value))
        "Template string contained unsupported keys ['doesnotexist']. Supported keys are: ['uuid4', 'now', 'utcnow']"

    """
    def __init__(self):
        # import datetime as datetime_mod
        # mapping of key to functions that build content for those keys
        self.builders = {
            'uuid4': uuid.uuid4,
            'now': now_local,
            'utcnow': now_utc,
        }

    def register(self, key):
        """
        Decorator which will register a function called when the template
        string contains ``key``.
        """
        def _decor(func):
            self.builders[key] = func
            return func
        return _decor

    def _build_fmtkw(self, template, **kwargs):
        """
        Builds the dictionary that can be passed to :func:`str.format`.
        """
        builders = self.builders | kwargs

        # Build the information requested for the file string.
        formatter = string.Formatter()
        fmtiter = formatter.parse(template)
        fmtkw = {}
        missing = []
        for fmttup in fmtiter:
            key = fmttup[1]

            if key in builders:
                builder = builders[key]
                if callable(builder):
                    fmtkw[key] = builder()
                else:
                    fmtkw[key] = builder
            else:
                missing.append(key)
        if missing:
            raise KeyError(
                f'Template string contained unsupported keys {missing}. '
                f'Supported keys are: {list(builders.keys())}'
            )
        return fmtkw

    def format(self, template, **kwargs):
        """
        Substitutes known keys with dynamically constructed values
        """
        fmtkw = self._build_fmtkw(template, **kwargs)
        text = template.format(**fmtkw)
        return text
