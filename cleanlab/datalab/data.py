# Copyright (C) 2017-2023  Cleanlab Inc.
# This file is part of cleanlab.
#
# cleanlab is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# cleanlab is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with cleanlab.  If not, see <https://www.gnu.org/licenses/>.
"""Classes and methods for datasets that are loaded into Datalab."""

import os
from typing import Any, Callable, Dict, List, Mapping, Tuple, Union, cast, TYPE_CHECKING

try:
    import datasets
except ImportError as error:
    raise ImportError(
        "Cannot import datasets package. "
        "Please install it and try again, or just install cleanlab with "
        "all optional dependencies via: `pip install 'cleanlab[all]'`"
    ) from error
import numpy as np
import pandas as pd
from datasets.arrow_dataset import Dataset
from datasets import ClassLabel

from cleanlab.internal.validation import labels_to_array


if TYPE_CHECKING:  # pragma: no cover
    DatasetLike = Union[Dataset, pd.DataFrame, Dict[str, Any], List[Dict[str, Any]], str]


class DataFormatError(ValueError):
    """Exception raised when the data is not in a supported format."""

    def __init__(self, data: Any):
        self.data = data
        message = (
            f"Unsupported data type: {type(data)}\n"
            "Supported types: "
            "datasets.Dataset, pandas.DataFrame, dict, list, str"
        )
        super().__init__(message)


class DatasetDictError(ValueError):
    """Exception raised when a DatasetDict is passed to Datalab.

    Usually, this means that a dataset identifier was passed to Datalab, but
    the dataset is a DatasetDict, which contains multiple splits of the dataset.

    """

    def __init__(self):
        message = (
            "Please pass a single dataset, not a DatasetDict. "
            "Try specifying a split, e.g. `dataset = load_dataset('dataset', split='train')` "
            "then pass `dataset` to Datalab."
        )
        super().__init__(message)


class DatasetLoadError(ValueError):
    """Exception raised when a dataset cannot be loaded.

    Parameters
    ----------
    dataset_type: type
        The type of dataset that failed to load.
    """

    def __init__(self, dataset_type: type):
        message = f"Failed to load dataset from {dataset_type}.\n"
        super().__init__(message)


class Data:
    """
    Class that holds and validates datasets for Datalab.

    Internally, the data is stored as a datasets.Dataset object and the labels
    are integers (ranging from 0 to K-1, where K is the number of classes) stored
    in a numpy array.

    Parameters
    ----------
    data :
        Dataset to be audited by Datalab.
        Several formats are supported, which will internally be converted to a Dataset object.

        Supported formats:
            - datasets.Dataset
            - pandas.DataFrame
            - dict
                - keys are strings
                - values are arrays or lists of equal length
            - list
                - list of dictionaries with the same keys
            - str
                - path to a local file
                    - Text (.txt)
                    - CSV (.csv)
                    - JSON (.json)
                - or a dataset identifier on the Hugging Face Hub
            It checks if the string is a path to a file that exists locally, and if not,
            it assumes it is a dataset identifier on the Hugging Face Hub.

    label_name : Union[str, List[str]]
        Name of the label column in the dataset.

    Warnings
    --------
    Optional dependencies:

    - datasets :
        Dataset, DatasetDict and load_dataset are imported from datasets.
        This is an optional dependency of cleanlab, but is required for
        :py:class:`Datalab <cleanlab.datalab.datalab.Datalab>` to work.
    """

    def __init__(self, data: "DatasetLike", label_name: str) -> None:
        self._validate_data(data)
        self._label_name = label_name
        self._data = self._load_data(data)
        self._validate_data_and_labels(self._data, self._data[label_name])
        self._data_hash = hash(self._data)
        self._labels, self._label_map = _extract_labels(self._data, label_name)

    def _load_data(self, data: "DatasetLike") -> Dataset:
        """Checks the type of dataset and uses the correct loader method and
        assigns the result to the data attribute."""
        dataset_factory_map: Dict[type, Callable[..., Dataset]] = {
            Dataset: lambda x: x,
            pd.DataFrame: Dataset.from_pandas,
            dict: self._load_dataset_from_dict,
            list: self._load_dataset_from_list,
            str: self._load_dataset_from_string,
        }
        if not isinstance(data, tuple(dataset_factory_map.keys())):
            raise DataFormatError(data)
        return dataset_factory_map[type(data)](data)

    def __len__(self) -> int:
        return len(self._data)

    def __eq__(self, other) -> bool:
        if isinstance(other, Data):
            # Equality checks
            hashes = self._data_hash == other._data_hash
            labels = np.array_equal(self._labels, other._labels)
            label_names = self._label_name == other._label_name
            label_maps = self._label_map == other._label_map
            return all([hashes, labels, label_names, label_maps])
        return False

    def __hash__(self) -> int:
        return self._data_hash

    @property
    def class_names(self) -> list:
        return list(self._label_map.values())

    @staticmethod
    def _validate_data(data) -> None:
        if isinstance(data, datasets.DatasetDict):
            raise DatasetDictError()
        if not isinstance(data, (Dataset, pd.DataFrame, dict, list, str)):
            raise DataFormatError(data)

    @staticmethod
    def _validate_data_and_labels(data, labels) -> None:
        assert isinstance(labels, (np.ndarray, list))
        assert len(labels) == len(data)

    @staticmethod
    def _load_dataset_from_dict(data_dict: Dict[str, Any]) -> Dataset:
        try:
            return Dataset.from_dict(data_dict)
        except Exception as error:
            raise DatasetLoadError(dict) from error

    @staticmethod
    def _load_dataset_from_list(data_list: List[Dict[str, Any]]) -> Dataset:
        try:
            return Dataset.from_list(data_list)
        except Exception as error:
            raise DatasetLoadError(list) from error

    @staticmethod
    def _load_dataset_from_string(data_string: str) -> Dataset:
        if not os.path.exists(data_string):
            try:
                dataset = datasets.load_dataset(data_string)
                return cast(Dataset, dataset)
            except Exception as error:
                raise DatasetLoadError(str) from error

        factory: Dict[str, Callable[[str], Any]] = {
            ".txt": Dataset.from_text,
            ".csv": Dataset.from_csv,
            ".json": Dataset.from_json,
        }

        extension = os.path.splitext(data_string)[1]
        if extension not in factory:
            raise DatasetLoadError(type(data_string))

        dataset = factory[extension](data_string)
        dataset_cast = cast(Dataset, dataset)
        return dataset_cast


def _extract_labels(data: Dataset, label_name: str) -> Tuple[np.ndarray, Mapping]:
    """
    Picks out labels from the dataset and formats them to be [0, 1, ..., K-1]
    where K is the number of classes. Also returns a mapping from the formatted
    labels to the original labels in the dataset.

    Note: This function is not meant to be used directly. It is used by
    ``cleanlab.data.Data`` to extract the formatted labels from the dataset
    and stores them as attributes.

    Parameters
    ----------
    label_name : str
        Name of the column in the dataset that contains the labels.

    Returns
    -------
    formatted_labels : np.ndarray
        Labels in the format [0, 1, ..., K-1] where K is the number of classes.

    inverse_map : dict
        Mapping from the formatted labels to the original labels in the dataset.
    """

    labels = labels_to_array(data[label_name])  # type: ignore[assignment]
    if labels.ndim != 1:
        raise ValueError("labels must be 1D numpy array.")

    label_name_feature = data.features[label_name]
    if isinstance(label_name_feature, ClassLabel):
        label_map = {label: label_name_feature.str2int(label) for label in label_name_feature.names}
        formatted_labels = labels
    else:
        label_map = {label: i for i, label in enumerate(np.unique(labels))}
        formatted_labels = np.vectorize(label_map.get)(labels)
    inverse_map = {i: label for label, i in label_map.items()}

    return formatted_labels, inverse_map
