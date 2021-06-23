from torch.utils.data import Dataset
import numpy as np
import pandas as pd
import torch
from typing import List, Union, Optional
from flood_forecast.pre_dict import interpolate_dict
from flood_forecast.preprocessing.buil_dataset import get_data
from datetime import datetime
from flood_forecast.preprocessing.temporal_feats import feature_fix


class CSVDataLoader(Dataset):
    def __init__(
        self,
        file_path: str,
        forecast_history: int,
        forecast_length: int,
        target_col: List,
        relevant_cols: List,
        scaling=None,
        start_stamp: int = 0,
        end_stamp: int = None,
        gcp_service_key: Optional[str] = None,
        interpolate_param: bool = False,
        sort_column=None,
        scaled_cols=None,
        feature_params=None,
        id_series_col=None,
        no_scale=False,
        stav=False,

    ):
        """
        A data loader that takes a CSV file and properly batches for use in training/eval a PyTorch model
        :param file_path: The path to the CSV file you wish to use.
        :param forecast_history: This is the length of the historical time series data you wish to
                                utilize for forecasting
        :param forecast_length: The number of time steps to forecast ahead (for transformer this must
                                equal history_length)
        :param relevant_cols: Supply column names you wish to predict in the forecast (others will not be used)
        :param target_col: The target column or columns you to predict. If you only have one still use a list ['cfs']
        :param scaling: (highly reccomended) If provided should be a subclass of sklearn.base.BaseEstimator
        and sklearn.base.TransformerMixin) i.e StandardScaler,  MaxAbsScaler, MinMaxScaler, etc) Note without
        a scaler the loss is likely to explode and cause infinite loss which will corrupt weights
        :param start_stamp int: Optional if you want to only use part of a CSV for training, validation
                                or testing supply these
        :param end_stamp int: Optional if you want to only use part of a CSV for training, validation,
                            or testing supply these
        :param sort_column str: The column to sort the time series on prior to forecast.
        :param scaled_cols: The columns you want scaling applied to (if left blank will default to all columns)
        :param feature_params: These are the datetime features you want to create.
        :param no_scale: This means that the end labels will not be scaled when running
        """
        super().__init__()
        interpolate = interpolate_param
        self.forecast_history = forecast_history
        self.forecast_length = forecast_length
        self.series_col = id_series_col
        self.stav = stav
        print("interpolate should be below")
        self.local_file_path = get_data(file_path, gcp_service_key)
        df = pd.read_csv(self.local_file_path)
        if self.stav:
            self.next_dic = []
            for indx, dt in enumerate(df['datetime']):
                dtn = datetime.strptime(dt,"%Y/%m/%d %H:%M")
                if ((dtn.hour == 5) or (dtn.hour == 6) or (dtn.hour == 7)) and (len(df) - indx >= self.forecast_history + self.forecast_length + 1):
                    self.next_dic.append(dt)
        relevant_cols3 = []
        if sort_column:
            df[sort_column] = pd.to_datetime(df[sort_column])
            df = df.sort_values(by=sort_column)
            if feature_params:
                df, relevant_cols3 = feature_fix(feature_params, sort_column, df)
                print("Created datetime feature columns are: ")
        print(relevant_cols3)
        self.relevant_cols3 = relevant_cols3
        if interpolate:
            df = interpolate_dict[interpolate["method"]](df, **interpolate["params"])
        self.df = df[relevant_cols + relevant_cols3].copy()
        print("Now loading " + file_path)
        self.original_df = df
        self.scale = None
        if scaled_cols is None:
            scaled_cols = relevant_cols
        if start_stamp != 0 and end_stamp is not None:
            self.df = self.df[start_stamp:end_stamp]
        elif start_stamp != 0:
            self.df = self.df[start_stamp:]
        elif end_stamp is not None:
            self.df = self.df[:end_stamp]
        self.unscaled_df = self.df
        if scaling is not None:
            print("scaling now")
            self.scale = scaling.fit(self.df[scaled_cols])
            temp_df = self.scale.transform(self.df[scaled_cols])

            # We define a second scaler to scale the end output
            # back to normal as models might not necessarily predict
            # other present time series values.
            targ_scale_class = self.scale.__class__
            self.targ_scaler = targ_scale_class()
            self.df[target_col] = self.targ_scaler.fit_transform(self.df[target_col])

            self.df[scaled_cols] = temp_df
        if (len(self.df) - self.df.count()).max() != 0:
            print("Error nan values detected in data. Please run interpolate ffill or bfill on data")
        self.targ_col = target_col
        self.df.to_csv("temp_df.csv")
        self.no_scale = no_scale

    def __getitem__(self, idx):
        if self.stav:
            idx = self.next_dic[idx]
        rows = self.df.iloc[idx: self.forecast_history + idx]
        targs_idx_start = self.forecast_history + idx
        if self.no_scale:
            targ_rows = self.unscaled_df.iloc[targs_idx_start: self.forecast_length + targs_idx_start]
        else:
            targ_rows = self.df.iloc[
                targs_idx_start: self.forecast_length + targs_idx_start
            ]
        src_data = rows.to_numpy()
        src_data = torch.from_numpy(src_data).float()
        trg_dat = targ_rows.to_numpy()
        trg_dat = torch.from_numpy(trg_dat).float()
        return src_data, trg_dat

    def __len__(self) -> int:
        if self.stav:
            return len(self.next_dic)
        return (
            len(self.df.index) - self.forecast_history - self.forecast_length - 1
        )

    def inverse_scale(
        self, result_data: Union[torch.Tensor, pd.Series, np.ndarray]
    ) -> torch.Tensor:
        """Un-does the scaling of the data

        :param result_data: The data you want to unscale can handle multiple data types.
        :type result_data: Union[torch.Tensor, pd.Series, np.ndarray]
        :return: Returns the unscaled data as PyTorch tensor.
        :rtype: torch.Tensor
        """
        if isinstance(result_data, pd.Series) or isinstance(
            result_data, pd.DataFrame
        ):
            result_data_np = result_data.values
        if isinstance(result_data, torch.Tensor):
            if len(result_data.shape) > 2:
                result_data = result_data.permute(2, 0, 1).reshape(result_data.shape[2], -1)
                result_data = result_data.permute(1, 0)
            result_data_np = result_data.numpy()
        if isinstance(result_data, np.ndarray):
            result_data_np = result_data
        # print(type(result_data))
        if self.no_scale:
            return torch.from_numpy(result_data_np)
        return torch.from_numpy(
            self.targ_scaler.inverse_transform(result_data_np)
        )


class CSVTestLoader(CSVDataLoader):
    def __init__(
        self,
        df_path: str,
        forecast_total: int,
        use_real_precip=True,
        use_real_temp=True,
        target_supplied=True,
        interpolate=False,
        seperate_training_and_validation=False,
        sort_column_clone=None,
        **kwargs
    ):
        """
        :param str df_path:
        A data loader for the test data.
        """
        if "file_path" not in kwargs:
            kwargs["file_path"] = df_path
        super().__init__(**kwargs)
        df_path = get_data(df_path)
        self.original_df = pd.read_csv(df_path)
        if interpolate:
            self.original_df = interpolate_dict[interpolate["method"]](self.original_df, **interpolate["params"])
        if sort_column_clone:
            self.original_df = self.original_df.sort_values(by=sort_column_clone)
        print("CSV Path below")
        print(df_path)
        self.forecast_total = forecast_total
        self.use_real_temp = use_real_temp
        self.use_real_precip = use_real_precip
        self.target_supplied = target_supplied
        # Convert back to datetime and save index
        sort_col1 = sort_column_clone if sort_column_clone else "datetime"
        self.original_df[sort_col1] = self.original_df["datetime"].astype(
            "datetime64[ns]"
        )
        self.original_df["original_index"] = self.original_df.index
        if len(self.relevant_cols3) > 0:
            self.original_df[self.relevant_cols3] = self.df[self.relevant_cols3]

    def get_from_start_date(self, forecast_start: datetime):
        dt_row = self.original_df[
            self.original_df["datetime"] == forecast_start
        ]
        revised_index = dt_row.index[0]
        return self.__getitem__(revised_index - self.forecast_history)

    def __getitem__(self, idx):
        if self.target_supplied:
            historical_rows = self.df.iloc[idx: self.forecast_history + idx]
            target_idx_start = self.forecast_history + idx
            # Why aren't we using these
            # targ_rows = self.df.iloc[
            #     target_idx_start : self.forecast_total + target_idx_start
            # ]
            all_rows_orig = self.original_df.iloc[
                idx: self.forecast_total + target_idx_start
            ].copy()
            historical_rows = torch.from_numpy(historical_rows.to_numpy())
            return historical_rows.float(), all_rows_orig, target_idx_start

    def convert_real_batches(self, the_col: str, rows_to_convert):
        """
        A helper function to return properly divided precip and temp
        values to be stacked with forecasted cfs.
        """
        the_column = torch.from_numpy(rows_to_convert[the_col].to_numpy())
        chunks = [
            the_column[
                self.forecast_length * i: self.forecast_length * (i + 1)
            ]
            for i in range(len(the_column) // self.forecast_length + 1)
        ]
        return chunks

    def convert_history_batches(
        self, the_col: Union[str, List[str]], rows_to_convert: pd.DataFrame
    ):
        """A helper function to return dataframe in batches of
        size (history_len, num_features)

        Args:
            the_col (str): column names
            rows_to_convert (pd.Dataframe): rows in a dataframe
            to be converted into batches
        """
        the_column = torch.from_numpy(rows_to_convert[the_col].to_numpy())
        chunks = [
            the_column[
                self.forecast_history * i: self.forecast_history * (i + 1)
            ]
            for i in range(len(the_column) // self.forecast_history + 1)
        ]
        return chunks

    def __len__(self) -> int:
        return (
            len(self.df.index) - self.forecast_history - self.forecast_total - 1
        )


class AEDataloader(CSVDataLoader):
    def __init__(
            self,
            file_path: str,
            relevant_cols: List,
            scaling=None,
            start_stamp: int = 0,
            target_col: List = None,
            end_stamp: int = None,
            unsqueeze_dim: int = 1,
            interpolate_param=False,
            forecast_history=1,
            no_scale=True,
            sort_column=None):
        """
        A data loader class for autoencoders.
        Overrides __len__ and __getitem__ from generic dataloader.
        Also defaults forecast_history and forecast_length to 1. Since AE will likely only use one row.
        Same parameters as before.
        """
        super().__init__(file_path=file_path, forecast_history=forecast_history, forecast_length=1,
                         target_col=target_col, relevant_cols=relevant_cols, start_stamp=start_stamp,
                         end_stamp=end_stamp, sort_column=sort_column, interpolate_param=False, no_scale=no_scale,
                         scaling=scaling)
        self.unsqueeze_dim = unsqueeze_dim

    def __handle_params__():
        pass

    def __len__(self):
        return len(self.df.index) - 1 - self.forecast_history

    def __getitem__(self, idx: int, uuid: int = None, column_relevant: str = None):
        # Warning this assumes that data is
        if uuid:
            idx = self.original_df[self.original_df[column_relevant] == uuid].index.values.astype(int)[0]
        target = torch.from_numpy(self.df.iloc[idx: idx + self.forecast_history].to_numpy()).float()
        if target.shape[0] == 0:
            raise ValueError("The item was not found in the index please try again")
        return torch.from_numpy(self.df.iloc[idx: idx + self.forecast_history].to_numpy()).float(), target


class TemporalLoader(CSVDataLoader):
    def __init__(
            self,
            time_feats: List[str],
            kwargs):
        """[summary]

        :param time_feats: [description]
        :type time_feats: List[str]
        :param kwargs: [description]
        :type kwargs: [type]
        """
        super().__init__(**kwargs)
        self.time_feats = time_feats
        self.temporal_df = self.df[time_feats]
        self.other_feats = self.df.drop(columns=time_feats)

    @staticmethod
    def df_to_numpy(pandas_stuff: pd.DataFrame):
        return torch.from_numpy(pandas_stuff.to_numpy()).float()

    def __getitem__(self, idx: int):
        rows = self.other_feats.iloc[idx: self.forecast_history + idx]
        temporal_feats = self.temporal_df.iloc[idx: self.forecast_history + idx]
        targs_idx_start = self.forecast_history + idx
        targ_rows = self.other_feats.iloc[
            targs_idx_start: self.forecast_length + targs_idx_start
        ]
        tar_temporal_feats = self.temporal_df.iloc[targs_idx_start: self.forecast_length + targs_idx_start]
        src_data = self.df_to_numpy(rows)
        trg_data = self.df_to_numpy(targ_rows)
        temporal_feats = self.df_to_numpy(temporal_feats)
        tar_temp = self.df_to_numpy(tar_temporal_feats)
        return (src_data, temporal_feats), (tar_temp, trg_data)

    def __len__(self):
        return (
            len(self.df.index) - self.forecast_history - self.forecast_length - 1
        )


class TemporalTestLoader(CSVTestLoader):
    def __init__(self, time_feats, kwargs={}, decoder_step_len=None):
        """[summary]

        :param time_feats: [description]
        :type time_feats: [type]
        :param kwargs: [description], defaults to {}
        :type kwargs: dict, optional
        :param decoder_step_len: [description], defaults to None
        :type decoder_step_len: [type], optional
        """
        super().__init__(kwargs["df_path"], kwargs["forecast_total"], **kwargs["kwargs"])
        self.time_feats = time_feats
        self.temporal_df = self.df[time_feats]
        self.other_feats = self.df.drop(columns=time_feats)
        self.decoder_step_len = decoder_step_len

    @staticmethod
    def df_to_numpy(pandas_stuff: pd.DataFrame):
        return torch.from_numpy(pandas_stuff.to_numpy()).float()

    def __getitem__(self, idx):
        if self.target_supplied:
            historical_rows = self.df.iloc[idx: self.forecast_history + idx]
            target_idx_start = self.forecast_history + idx
            # Why aren't we using these
            # targ_rows = self.df.iloc[
            #     target_idx_start : self.forecast_total + target_idx_start
            historical_rows = self.other_feats.iloc[idx: self.forecast_history + idx]
            targs_idx_start = self.forecast_history + idx
            temporal_feat = self.temporal_df.iloc[idx: self.forecast_history + idx]
            end_idx = self.forecast_total + target_idx_start
            if self.decoder_step_len:
                end_idx = self.forecast_total + target_idx_start + self.decoder_step_len
                tar_temporal_feats = self.temporal_df.iloc[targs_idx_start: end_idx]
                targ_rows = self.other_feats.iloc[targs_idx_start: end_idx]
            else:
                tar_temporal_feats = self.temporal_df.iloc[targs_idx_start: end_idx]
                targ_rows = self.other_feats.iloc[targs_idx_start: end_idx]
            src_data = self.df_to_numpy(historical_rows)
            trg_data = self.df_to_numpy(targ_rows)
            temporal_feat = self.df_to_numpy(temporal_feat)
            tar_temp = self.df_to_numpy(tar_temporal_feats)
            all_rows_orig = self.original_df.iloc[
                idx: self.forecast_total + target_idx_start
            ].copy()
            historical_rows = torch.from_numpy(historical_rows.to_numpy())
            return (src_data, temporal_feat), (tar_temp, trg_data), all_rows_orig, target_idx_start
