# flake8: noqa
import argparse
from typing import Dict
import json
import plotly.graph_objects as go
import wandb
import pandas as pd
from flood_forecast.pytorch_training import train_transformer_style
from flood_forecast.time_model import PyTorchForecast
from flood_forecast.evaluator import evaluate_model
from flood_forecast.time_model import scaling_function
from flood_forecast.plot_functions import (
    plot_df_test_with_confidence_interval,
    plot_df_test_with_probabilistic_confidence_interval)


def train_function(model_type: str, params: Dict):
    """Function to train a Model(TimeSeriesModel) or da_rnn. Will return the trained model
    :param model_type: Type of the model. In almost all cases this will be 'PyTorch'
    :type model_type: str
    :param params: Dictionary containing all the parameters needed to run the model
    :type Dict:
    """
    dataset_params = params["dataset_params"]
    if model_type == "da_rnn":
        from flood_forecast.da_rnn.train_da import da_rnn, train
        from flood_forecast.preprocessing.preprocess_da_rnn import make_data
        preprocessed_data = make_data(
            params["dataset_params"]["training_path"],
            params["dataset_params"]["target_col"],
            params["dataset_params"]["forecast_length"])
        config, model = da_rnn(preprocessed_data, len(dataset_params["target_col"]))
        # All train functions return trained_model
        trained_model = train(model, preprocessed_data, config)
    elif model_type == "PyTorch":
        dataset_params["batch_size"] = params["training_params"]["batch_size"]
        trained_model = PyTorchForecast(
            params["model_name"],
            dataset_params["training_path"],
            dataset_params["validation_path"],
            dataset_params["test_path"],
            params)
        takes_target = False
        if "takes_target" in trained_model.params:
            takes_target = trained_model.params["takes_target"]
        if "dataset_params" not in trained_model.params["inference_params"]:
            print("Using generic dataset params")
            trained_model.params["inference_params"]["dataset_params"] = trained_model.params["dataset_params"].copy()
            del trained_model.params["inference_params"]["dataset_params"]["class"]
            # noqa: F501
            trained_model.params["inference_params"]["dataset_params"]["interpolate_param"] = trained_model.params["inference_params"]["dataset_params"].pop("interpolate")
            trained_model.params["inference_params"]["dataset_params"]["scaling"] = trained_model.params["inference_params"]["dataset_params"].pop("scaler")
            trained_model.params["inference_params"]["dataset_params"]["feature_params"] = trained_model.params["inference_params"]["dataset_params"].pop("feature_param")
            delete_params = ["num_workers", "pin_memory", "train_start", "train_end", "valid_start", "valid_end", "test_start", "test_end",
                            "training_path", "validation_path", "test_path", "batch_size"]
            for param in delete_params:
                if param in trained_model.params["inference_params"]["dataset_params"]:
                    del trained_model.params["inference_params"]["dataset_params"][param]
        train_transformer_style(model=trained_model,
                                training_params=params["training_params"],
                                takes_target=takes_target,
                                forward_params={})
        # print("stage_1")
        # To do delete
        if "scaler" in dataset_params:
            # print("stage_2")
            if "scaler_params" in dataset_params:
                # print("stage_3")
                params["inference_params"]["dataset_params"]["scaling"] = scaling_function({},
                                                                                           dataset_params)["scaling"]
            else:
                # print("stage_4")
                params["inference_params"]["dataset_params"]["scaling"] = scaling_function({},
                                                                                           dataset_params)["scaling"]
            # print("stage_5")
            params["inference_params"]["dataset_params"].pop('scaler_params', None)
            dt_starts = params["inference_params"]["datetime_start"] 
        test_acc0 = []
        for dt_start in dt_starts:
            params["inference_params"]["datetime_start"] = dt_start
            print("current datetime start:",dt_start)
            test_acc = evaluate_model(
                trained_model,
                model_type,
                params["dataset_params"]["target_col"],
                params["metrics"],
                params["inference_params"],
                {})
            # print("stage_6")
            test_acc0.append(test_acc[0])
            #wandb.run.summary["test_accuracy"] = test_acc[0]
            # print("stage_7")
            df_train_and_test = test_acc[1]
            forecast_start_idx = test_acc[2]
            df_prediction_samples = test_acc[3]
            # print("stage_8")
            mae = (df_train_and_test.loc[forecast_start_idx:, "preds"] -
                df_train_and_test.loc[forecast_start_idx:, params["dataset_params"]["target_col"][0]]).abs()
            # print("stage_9")
            inverse_mae = 1 / mae
            i = 0
            for df in df_prediction_samples:
                pred_std = df.std(axis=1)
                average_prediction_sharpe = (inverse_mae / pred_std).mean()
                wandb.log({'average_prediction_sharpe' + str(i): average_prediction_sharpe})
                i += 1
            # print("stage_10")
            df_train_and_test.to_csv("temp_preds.csv")
            # print("stage_11")
            # Log plots now
            if "probabilistic" in params["inference_params"]:
                # print("stage_12")
                test_plot = plot_df_test_with_probabilistic_confidence_interval(
                    df_train_and_test,
                    forecast_start_idx,
                    params,)
            elif len(df_prediction_samples) > 0:
                for thing in zip(df_prediction_samples, params["dataset_params"]["target_col"]):
                    thing[0].to_csv(thing[1] + ".csv")
                    # print("stage_13")
                    test_plot = plot_df_test_with_confidence_interval(
                        df_train_and_test,
                        thing[0],
                        forecast_start_idx,
                        params,
                        targ_col=thing[1],
                        ci=95,
                        alpha=0.25)
                    wandb.log({"test_plot_" + thing[1]: test_plot})
            else:
                # print("stage_14")
                pd.options.plotting.backend = "plotly"
                t = params["dataset_params"]["target_col"][0]
                test_plot = df_train_and_test[[t, "preds"]].plot()
                wandb.log({"test_plot_" + t + 'datetime_start_from_' + dt_start: test_plot})
            print("Now plotting final plots")
            test_plot_all = go.Figure()
            # print("stage_15")
            for relevant_col in params["dataset_params"]["relevant_cols"]:
                test_plot_all.add_trace(
                    go.Scatter(
                        x=df_train_and_test.index,
                        y=df_train_and_test[relevant_col],
                        name=relevant_col))
            wandb.log({"test_plot_all_datetime_start_from_" + dt_start: test_plot_all})
        ta0_sum = 0
        for ta0 in test_acc0:
            ta0_sum += ta0["OT_RMSELoss"]
        wandb.run.summary["test_accuracy"] = {"OT_RMSELoss":ta0_sum / len(test_acc0)}
        wandb.run.summary["test_accuracy_for_each"] = test_acc0
    else:
        raise Exception("Please supply valid model type for forecasting")
    return trained_model


def main():
    """
    Main function which is called from the command line. Entrypoint for training all ML models.
    """
    parser = argparse.ArgumentParser(description="Argument parsing for training and eval")
    parser.add_argument("-p", "--params", help="Path to model config file")
    args = parser.parse_args()
    with open(args.params) as f:
        training_config = json.load(f)
    train_function(training_config["model_type"], training_config)
    # evaluate_model(trained_model)
    print("Process is now complete.")

if __name__ == "__main__":
    main()
