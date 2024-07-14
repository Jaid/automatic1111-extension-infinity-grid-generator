##################
# Stable Diffusion Infinity Grid Generator
#
# Author: Alex 'mcmonkey' Goodwin
# GitHub URL: https://github.com/mcmonkeyprojects/sd-infinity-grid-generator-script
# Created: 2022/12/08
# Last updated: 2024/07/05
# License: MIT
#
# For usage help, view the README.md file in the extension root, or via the GitHub page.
#
##################

import gradio as gr
import os, numpy, threading
from copy import copy
from datetime import datetime
from modules import images, shared, sd_models, sd_vae, sd_samplers, scripts, processing, ui_components
from modules.processing import process_images, Processed
from modules.shared import opts, state
from PIL import Image
import gridgencore as core
from gridgencore import clean_name, clean_mode, get_best_in_list, choose_better_file_name, GridSettingMode, fix_num, apply_field, registerMode

######################### Constants #########################
refresh_symbol = '\U0001f504'  # 🔄
fill_values_symbol = "\U0001f4d2"  # 📒
INF_GRID_README = "https://github.com/mcmonkeyprojects/sd-infinity-grid-generator-script"
core.EXTRA_FOOTER = 'Images area auto-generated by an AI (Stable Diffusion) and so may not have been reviewed by the page author before publishing.\n<script src="a1111webui.js?vary=9"></script>'
core.EXTRA_ASSETS = ["a1111webui.js"]

######################### Value Mode Helpers #########################

def get_model_for(name):
    return get_best_in_list(name, map(lambda m: m.title, sd_models.checkpoints_list.values()))

def apply_model(p, v):
    opts.sd_model_checkpoint = get_model_for(v)
    sd_models.reload_model_weights()

def clean_model(p, v):
    actual_model = get_model_for(v)
    if actual_model is None:
        raise RuntimeError(f"Invalid parameter '{p}' as '{v}': model name unrecognized - valid {list(map(lambda m: m.title, sd_models.checkpoints_list.values()))}")
    return choose_better_file_name(v, actual_model)

def get_vae_for(name):
    return get_best_in_list(name, sd_vae.vae_dict.keys())

def apply_vae(p, v):
    vae_name = clean_name(v)
    if vae_name == "none":
        vae_name = "None"
    elif vae_name in ["auto", "automatic"]:
        vae_name = "Automatic"
    else:
        vae_name = get_vae_for(vae_name)
    opts.sd_vae = vae_name
    sd_vae.reload_vae_weights(None)

def clean_vae(p, v):
    vae_name = clean_name(v)
    if vae_name in ["none", "auto", "automatic"]:
        return vae_name
    actual_vae = get_vae_for(vae_name)
    if actual_vae is None:
        raise RuntimeError(f"Invalid parameter '{p}' as '{v}': VAE name unrecognized - valid: {list(sd_vae.vae_dict.keys())}")
    return choose_better_file_name(v, actual_vae)

def apply_codeformer_weight(p, v):
    opts.code_former_weight = float(v)

def apply_restore_faces(p, v):
    input = str(v).lower().strip()
    if input == "false":
        p.restore_faces = False
        return
    p.restore_faces = True
    restorer = get_best_in_list(input, map(lambda m: m.name(), shared.face_restorers))
    if restorer is not None:
        opts.face_restoration_model = restorer

def prompt_replace_parse_list(in_list):
    if not any(('=' in x) for x in in_list):
        first_val = in_list[0]
        for x in range(0, len(in_list)):
            in_list[x] = {
                "title": in_list[x],
                "params": {
                    "promptreplace": f"{first_val}={in_list[x]}"
                }
            }
    return in_list

def apply_prompt_replace(p, v):
    multiPromptReplaceToken = '&&'
    replacementInstructions = [x.strip() for x in v.split(multiPromptReplaceToken)]
    for replacementInstruction in replacementInstructions:
        val = v.split('=', maxsplit=1)
        if len(val) != 2:
            raise RuntimeError(f"Invalid prompt replace, missing '=' symbol, for '{replacementInstruction}'")
        match = val[0].strip()
        replace = val[1].strip()
        if Script.VALIDATE_REPLACE and match not in p.prompt and match not in p.negative_prompt:
            raise RuntimeError(f"Invalid prompt replace, '{match}' is not in prompt '{p.prompt}' nor negative prompt '{p.negative_prompt}'")
        p.prompt = p.prompt.replace(match, replace)
        p.negative_prompt = p.negative_prompt.replace(match, replace)

def apply_enable_hr(p, v):
    p.enable_hr = v
    if v:
        if p.denoising_strength is None:
            p.denoising_strength = 0.75

def apply_styles(p, v: str):
    p.styles = list(v.split(','))

def apply_setting_override(name: str):
    def applier(p, v):
        p.override_settings[name] = v
    return applier

######################### Value Modes #########################
has_inited = False

def try_init():
    global has_inited
    if has_inited:
        return
    has_inited = True
    core.grid_call_init_hook = a1111_grid_call_init_hook
    core.grid_call_param_add_hook = a1111_grid_call_param_add_hook
    core.grid_call_apply_hook = a1111_grid_call_apply_hook
    core.grid_runner_pre_run_hook = a1111_grid_runner_pre_run_hook
    core.grid_runner_pre_dry_hook = a1111_grid_runner_pre_dry_hook
    core.grid_runner_post_dry_hook = a1111_grid_runner_post_dry_hook
    core.grid_runner_count_steps = a1111_grid_runner_count_steps
    core.webdata_get_base_param_data = a1111_webdata_get_base_param_data
    registerMode("Model", GridSettingMode(dry=False, type="text", apply=apply_model, clean=clean_model, valid_list=lambda: list(map(lambda m: m.title, sd_models.checkpoints_list.values()))))
    registerMode("VAE", GridSettingMode(dry=False, type="text", apply=apply_vae, clean=clean_vae, valid_list=lambda: list(sd_vae.vae_dict.keys()) + ['none', 'auto', 'automatic']))
    registerMode("Sampler", GridSettingMode(dry=True, type="text", apply=apply_field("sampler_name"), valid_list=lambda: list(sd_samplers.all_samplers_map.keys())))
    registerMode("Scheduler", GridSettingMode(dry=True, type="text", apply=apply_field("scheduler"), valid_list=lambda: list(shared.sd_schedulers.schedulers_map.keys())))
    registerMode("Seed", GridSettingMode(dry=True, type="integer", apply=apply_field("seed")))
    registerMode("Steps", GridSettingMode(dry=True, type="integer", min=0, max=200, apply=apply_field("steps")))
    registerMode("CFG Scale", GridSettingMode(dry=True, type="decimal", min=0, max=500, apply=apply_field("cfg_scale")))
    registerMode("Width", GridSettingMode(dry=True, type="integer", apply=apply_field("width")))
    registerMode("Height", GridSettingMode(dry=True, type="integer", apply=apply_field("height")))
    registerMode("Prompt", GridSettingMode(dry=True, type="text", apply=apply_field("prompt")))
    registerMode("Negative Prompt", GridSettingMode(dry=True, type="text", apply=apply_field("negative_prompt")))
    registerMode("Prompt Replace", GridSettingMode(dry=True, type="text", apply=apply_prompt_replace, parse_list=prompt_replace_parse_list))
    registerMode("Styles", GridSettingMode(dry=True, type="text", apply=apply_styles, valid_list=lambda: list(shared.prompt_styles.styles)))
    registerMode("Var Seed", GridSettingMode(dry=True, type="integer", apply=apply_field("subseed")))
    registerMode("Var Strength", GridSettingMode(dry=True, type="decimal", min=0, max=1, apply=apply_field("subseed_strength")))
    registerMode("ClipSkip", GridSettingMode(dry=False, type="integer", min=1, max=12, apply=apply_setting_override("CLIP_stop_at_last_layers")))
    registerMode("Denoising", GridSettingMode(dry=True, type="decimal", min=0, max=1, apply=apply_field("denoising_strength")))
    registerMode("ETA", GridSettingMode(dry=True, type="decimal", min=0, max=1, apply=apply_field("eta")))
    registerMode("Sigma Churn", GridSettingMode(dry=True, type="decimal", min=0, max=1, apply=apply_field("s_churn")))
    registerMode("Sigma TMin", GridSettingMode(dry=True, type="decimal", min=0, max=1, apply=apply_field("s_tmin")))
    registerMode("Sigma TMax", GridSettingMode(dry=True, type="decimal", min=0, max=1, apply=apply_field("s_tmax")))
    registerMode("Sigma Noise", GridSettingMode(dry=True, type="decimal", min=0, max=1, apply=apply_field("s_noise")))
    registerMode("Out Width", GridSettingMode(dry=True, type="integer", min=0, apply=apply_field("inf_grid_out_width")))
    registerMode("Out Height", GridSettingMode(dry=True, type="integer", min=0, apply=apply_field("inf_grid_out_height")))
    registerMode("Restore Faces", GridSettingMode(dry=True, type="text", apply=apply_restore_faces, valid_list=lambda: list(map(lambda m: m.name(), shared.face_restorers)) + ["true", "false"]))
    registerMode("CodeFormer Weight", GridSettingMode(dry=True, type="decimal", min=0, max=1, apply=apply_codeformer_weight))
    registerMode("Tiling", GridSettingMode(dry=True, type="boolean", apply=apply_field("tiling")))
    registerMode("Image Mask Weight", GridSettingMode(dry=True, type="decimal", min=0, max=1, apply=apply_field("inpainting_mask_weight")))
    registerMode("ETA Noise Seed Delta", GridSettingMode(dry=True, type="integer", apply=apply_setting_override("eta_noise_seed_delta")))
    registerMode("Enable HighRes Fix", GridSettingMode(dry=True, type="boolean", apply=apply_enable_hr))
    registerMode("HighRes Scale", GridSettingMode(dry=True, type="decimal", min=1, max=16, apply=apply_field("hr_scale")))
    registerMode("HighRes Steps", GridSettingMode(dry=True, type="integer", min=0, max=200, apply=apply_field("hr_second_pass_steps")))
    registerMode("HighRes Resize Width", GridSettingMode(dry=True, type="integer", apply=apply_field("hr_resize_x")))
    registerMode("HighRes Resize Height", GridSettingMode(dry=True, type="integer", apply=apply_field("hr_resize_y")))
    registerMode("HighRes Upscale to Width", GridSettingMode(dry=True, type="integer", apply=apply_field("hr_upscale_to_x")))
    registerMode("HighRes Upscale to Height", GridSettingMode(dry=True, type="integer", apply=apply_field("hr_upscale_to_y")))
    registerMode("HighRes Upscaler", GridSettingMode(dry=True, type="text", apply=apply_field("hr_upscaler"), valid_list=lambda: list(map(lambda u: u.name, shared.sd_upscalers)) + list(shared.latent_upscale_modes.keys())))
    registerMode("HighRes Sampler", GridSettingMode(dry=True, type="text", apply=apply_field("hr_sampler_name"), valid_list=lambda: list(sd_samplers.all_samplers_map.keys())))
    registerMode("HighRes Checkpoint", GridSettingMode(dry=False, type="text", apply=apply_field("hr_checkpoint_name"), clean=clean_model, valid_list=lambda: list(map(lambda m: m.title, sd_models.checkpoints_list.values()))))
    registerMode("Image CFG Scale", GridSettingMode(dry=True, type="decimal", min=0, max=500, apply=apply_field("image_cfg_scale")))
    registerMode("Use Result Index", GridSettingMode(dry=True, type="integer", min=0, max=500, apply=apply_field("inf_grid_use_result_index")))
    try:
        script_list = [x for x in scripts.scripts_data if x.script_class.__module__ == "dynamic_thresholding.py"][:1]
        if len(script_list) == 1:
            dynamic_thresholding = script_list[0].module
            registerMode("[DynamicThreshold] Enable", GridSettingMode(dry=True, type="boolean", apply=apply_field("dynthres_enabled")))
            registerMode("[DynamicThreshold] Mimic Scale", GridSettingMode(dry=True, type="decimal", min=0, max=500, apply=apply_field("dynthres_mimic_scale")))
            registerMode("[DynamicThreshold] Threshold Percentile", GridSettingMode(dry=True, type="decimal", min=0.0, max=100.0, apply=apply_field("dynthres_threshold_percentile")))
            registerMode("[DynamicThreshold] Mimic Mode", GridSettingMode(dry=True, type="text", apply=apply_field("dynthres_mimic_mode"), valid_list=lambda: list(dynamic_thresholding.VALID_MODES)))
            registerMode("[DynamicThreshold] CFG Mode", GridSettingMode(dry=True, type="text", apply=apply_field("dynthres_cfg_mode"), valid_list=lambda: list(dynamic_thresholding.VALID_MODES)))
            registerMode("[DynamicThreshold] Mimic Scale Minimum", GridSettingMode(dry=True, type="decimal", min=0.0, max=100.0, apply=apply_field("dynthres_mimic_scale_min")))
            registerMode("[DynamicThreshold] CFG Scale Minimum", GridSettingMode(dry=True, type="decimal", min=0.0, max=100.0, apply=apply_field("dynthres_cfg_scale_min")))
            registerMode("[DynamicThreshold] Experiment Mode", GridSettingMode(dry=True, type="decimal", min=0, max=100000, apply=apply_field("dynthres_experiment_mode")))
            registerMode("[DynamicThreshold] Scheduler Value", GridSettingMode(dry=True, type="decimal", min=0, max=100, apply=apply_field("dynthres_scheduler_val")))
            registerMode("[DynamicThreshold] Scaling Startpoint", GridSettingMode(dry=True, type="text", apply=apply_field("dynthres_scaling_startpoint"), valid_list=lambda: list(['ZERO', 'MEAN'])))
            registerMode("[DynamicThreshold] Variability Measure", GridSettingMode(dry=True, type="text", apply=apply_field("dynthres_variability_measure"), valid_list=lambda: list(['STD', 'AD'])))
            registerMode("[DynamicThreshold] Interpolate Phi", GridSettingMode(dry=True, type="decimal", min=0, max=1, apply=apply_field("dynthres_interpolate_phi")))
            registerMode("[DynamicThreshold] Separate Feature Channels", GridSettingMode(dry=True, type="boolean", apply=apply_field("dynthres_separate_feature_channels")))
        script_list = [x for x in scripts.scripts_data if x.script_class.__module__ == "controlnet.py"][:1]
        if len(script_list) == 1:
            # Hacky but works
            module = script_list[0].module
            preprocessors_list = list(p.name for p in module.Preprocessor.get_sorted_preprocessors())
            def validate_param(p, v):
                if not shared.opts.data.get("control_net_allow_script_control", False):
                    raise RuntimeError("ControlNet options cannot currently work, you must enable 'Allow other script to control this extension' in Settings -> ControlNet first")
                return v
            registerMode("[ControlNet] Enable", GridSettingMode(dry=True, type="boolean", apply=apply_field("control_net_enabled"), clean=validate_param))
            registerMode("[ControlNet] Preprocessor", GridSettingMode(dry=True, type="text", apply=apply_field("control_net_module"), clean=validate_param, valid_list=lambda: list(preprocessors_list)))
            registerMode("[ControlNet] Model", GridSettingMode(dry=True, type="text", apply=apply_field("control_net_model"), clean=validate_param, valid_list=lambda: list(list(module.global_state.cn_models.keys()))))
            registerMode("[ControlNet] Weight", GridSettingMode(dry=True, type="decimal", min=0.0, max=2.0, apply=apply_field("control_net_weight"), clean=validate_param))
            registerMode("[ControlNet] Guidance Strength", GridSettingMode(dry=True, type="decimal", min=0.0, max=1.0, apply=apply_field("control_net_guidance_strength"), clean=validate_param))
            registerMode("[ControlNet] Annotator Resolution", GridSettingMode(dry=True, type="integer", min=0, max=2048, apply=apply_field("control_net_pres"), clean=validate_param))
            registerMode("[ControlNet] Threshold A", GridSettingMode(dry=True, type="integer", min=0, max=256, apply=apply_field("control_net_pthr_a"), clean=validate_param))
            registerMode("[ControlNet] Threshold B", GridSettingMode(dry=True, type="integer", min=0, max=256, apply=apply_field("control_net_pthr_b"), clean=validate_param))
            registerMode("[ControlNet] Image", GridSettingMode(dry=True, type="text", apply=core.apply_field_as_image_data("control_net_input_image"), clean=validate_param, valid_list=lambda: core.list_image_files()))
    except Exception as e:
        print(f"Infinity Grid Generator failed to import a dependency module: {e}")
        pass

######################### Actual Execution Logic #########################

def a1111_grid_call_init_hook(grid_call: core.SingleGridCall):
    grid_call.replacements = list()

def a1111_grid_call_param_add_hook(grid_call: core.SingleGridCall, param: str, value):
    if grid_call.grid.min_width is None:
        grid_call.grid.min_width = grid_call.grid.initial_p.width
    if grid_call.grid.min_height is None:
        grid_call.grid.min_height = grid_call.grid.initial_p.height
    cleaned = clean_mode(param)
    if cleaned == "promptreplace":
        grid_call.replacements.append(value)
        return True
    elif cleaned in ["width", "outwidth"]:
        grid_call.grid.min_width = min(grid_call.grid.min_width or 99999, int(value))
    elif cleaned in ["height", "outheight"]:
        grid_call.grid.min_height = min(grid_call.grid.min_height or 99999, int(value))
    return False

def a1111_grid_call_apply_hook(grid_call: core.SingleGridCall, param: str, dry: bool):
    for replace in grid_call.replacements:
        apply_prompt_replace(param, replace)

def a1111_grid_runner_pre_run_hook(grid_runner: core.GridRunner):
    state.job_count = grid_runner.total_run
    shared.total_tqdm.updateTotal(grid_runner.total_steps)
    # prevents the steps from from being recalculated by Auto1 using the current value of hires steps
    state.processing_has_refined_job_count = True

class TempHolder: pass

def a1111_grid_runner_pre_dry_hook(grid_runner: core.GridRunner):
    grid_runner.temp = TempHolder()
    grid_runner.temp.old_codeformer_weight = opts.code_former_weight
    grid_runner.temp.old_face_restorer = opts.face_restoration_model
    grid_runner.temp.old_vae = opts.sd_vae
    grid_runner.temp.old_model = opts.sd_model_checkpoint

def a1111_grid_runner_post_dry_hook(grid_runner: core.GridRunner, p, set):
    p.seed = processing.get_fixed_seed(p.seed)
    p.subseed = processing.get_fixed_seed(p.subseed)
    processed = process_images(p)
    if len(processed.images) < 1:
        raise RuntimeError(f"Something went wrong! Image gen '{set.data}' produced {len(processed.images)} images, which is wrong")
    os.makedirs(os.path.dirname(set.filepath), exist_ok=True)
    result_index = getattr(p, 'inf_grid_use_result_index', 0)
    if result_index >= len(processed.images):
        result_index = len(processed.images) - 1
    img = processed.images[result_index]
    if type(img) == numpy.ndarray:
        img = Image.fromarray(img)
    if hasattr(p, 'inf_grid_out_width') and hasattr(p, 'inf_grid_out_height'):
        img = img.resize((p.inf_grid_out_width, p.inf_grid_out_height), resample=images.LANCZOS)
    processed.images[result_index] = img
    info = processing.create_infotext(p, [p.prompt], [p.seed], [p.subseed], [])
    ext = grid_runner.grid.format
    prompt = p.prompt
    seed = processed.seed
    def save_offthread():
        images.save_image(img, path=os.path.dirname(set.filepath), basename="", forced_filename=os.path.basename(set.filepath), save_to_dirs=False, info=info, extension=ext, p=p, prompt=prompt, seed=seed)
    threading.Thread(target=save_offthread).start()
    opts.code_former_weight = grid_runner.temp.old_codeformer_weight
    opts.face_restoration_model = grid_runner.temp.old_face_restorer
    opts.sd_vae = grid_runner.temp.old_vae
    opts.sd_model_checkpoint = grid_runner.temp.old_model
    grid_runner.temp = None
    return processed

def a1111_grid_runner_count_steps(grid_runner: core.GridRunner, set):
    step_count = set.params.get("steps")
    step_count = int(step_count) if step_count is not None else grid_runner.p.steps
    total_steps = step_count
    enable_hr = set.params.get("enable highres fix")
    if enable_hr is None:
        enable_hr = grid_runner.p.enable_hr if hasattr(grid_runner.p, 'enable_hr') else False
    if enable_hr:
        highres_steps = set.params.get("highres steps")
        highres_steps = int(highres_steps) if highres_steps is not None else (grid_runner.p.hr_second_pass_steps or step_count)
        total_steps += highres_steps
    return total_steps

def a1111_webdata_get_base_param_data(p):
    return {
        "sampler": p.sampler_name,
        "scheduler": p.scheduler,
        "seed": p.seed,
        "restorefaces": (opts.face_restoration_model if p.restore_faces else None),
        "steps": p.steps,
        "cfgscale": p.cfg_scale,
        "model": choose_better_file_name('', shared.sd_model.sd_checkpoint_info.model_name).replace(',', '').replace(':', ''),
        "vae": (None if sd_vae.loaded_vae_file is None else (choose_better_file_name('', sd_vae.loaded_vae_file).replace(',', '').replace(':', ''))),
        "width": p.width,
        "height": p.height,
        "prompt": p.prompt,
        "negativeprompt": p.negative_prompt,
        "varseed": (None if p.subseed_strength == 0 else p.subseed),
        "varstrength": (None if p.subseed_strength == 0 else p.subseed_strength),
        "clipskip": opts.CLIP_stop_at_last_layers,
        "codeformerweight": opts.code_former_weight,
        "denoising": getattr(p, 'denoising_strength', None),
        "eta": fix_num(p.eta),
        "sigmachurn": fix_num(p.s_churn),
        "sigmatmin": fix_num(p.s_tmin),
        "sigmatmax": fix_num(p.s_tmax),
        "sigmanoise": fix_num(p.s_noise),
        "ENSD": None if opts.eta_noise_seed_delta == 0 else opts.eta_noise_seed_delta
    }

class SettingsFixer():
    def __enter__(self):
        self.model = opts.sd_model_checkpoint
        self.code_former_weight = opts.code_former_weight
        self.face_restoration_model = opts.face_restoration_model
        self.vae = opts.sd_vae

    def __exit__(self, exc_type, exc_value, tb):
        opts.code_former_weight = self.code_former_weight
        opts.face_restoration_model = self.face_restoration_model
        opts.sd_vae = self.vae
        opts.sd_model_checkpoint = self.model
        sd_models.reload_model_weights()
        sd_vae.reload_vae_weights()

######################### Script class entrypoint #########################
class Script(scripts.Script):
    BASEDIR = scripts.basedir()
    VALIDATE_REPLACE = True

    def title(self):
        return "Generate Infinite-Axis Grid"

    def show(self, is_img2img):
        return True

    def ui(self, is_img2img):
        core.list_image_files()
        try_init()
        gr.HTML(value=f"<br>Confused/new? View <a style=\"border-bottom: 1px #00ffff dotted;\" href=\"{INF_GRID_README}\" target=\"_blank\" rel=\"noopener noreferrer\">the README</a> for usage instructions.<br><br>")
        with gr.Row():
            grid_file = gr.Dropdown(value="Create in UI",label="Select grid definition file", choices=["Create in UI"] + core.get_name_list())
            def refresh():
                new_choices = ["Create in UI"] + core.get_name_list()
                grid_file.choices = new_choices
                return gr.update(choices=new_choices)
            refresh_button = ui_components.ToolButton(value=refresh_symbol, elem_id="infinity_grid_refresh_button")
            refresh_button.click(fn=refresh, inputs=[], outputs=[grid_file])
        output_file_path = gr.Textbox(value="", label="Output folder name (if blank uses yaml's 'outpath' parameter, filename, or current date)")
        page_will_be = gr.HTML(value="(...)<br><br>")
        manual_group = gr.Group(visible=True)
        manual_axes = list()
        sets = list()
        def get_page_url_text(file):
            if file is None:
                return "(...)"
            notice = ""
            if not os.path.isabs(file):
                out_path = opts.outdir_grids or (opts.outdir_img2img_grids if is_img2img else opts.outdir_txt2img_grids)
                full_out_path = out_path + "/" + file
                url = "/file=" + full_out_path
            else:
                full_out_path = file
                url = "file://" + ("" if file.startswith("/") else "/") + file
                notice = "<br><span style=\"color: red;\">This is a raw file path, not within the WebUI output directory. You may need to open the output file manually.</span>"
            if os.path.exists(full_out_path):
                notice += "<br><span style=\"color: red;\">NOTICE: There is already something saved there! This will overwrite prior data.</span>"
            return f"Page will be at <a style=\"border-bottom: 1px #00ffff dotted;\" href=\"{url}/index.html\" target=\"_blank\" rel=\"noopener noreferrer\">(Click me) <code>{full_out_path}</code></a>{notice}<br><br>"
        def update_page_url(file_path, selected_file):
            out_file_update = gr.Textbox.update()
            if file_path == "" and selected_file == "Create in UI":
                file_path = f"autonamed_inf_grid_{datetime.now().strftime('%Y_%m_%d_%H_%M_%S')}"
                out_file_update = gr.Textbox.update(value=file_path)
            info_update = gr.update(value=get_page_url_text(file_path or (selected_file.replace(".yml", "") if selected_file is not None else None)))
            return [out_file_update, info_update]
        def update_page_url_single(file_path, selected_file):
            (_, info_update) = update_page_url(file_path, selected_file)
            return info_update
        with manual_group:
            with gr.Row():
                with gr.Column():
                    axis_count = 0
                    for group in range(0, 4):
                        group_obj = gr.Group(visible=group == 0)
                        with group_obj:
                            rows = list()
                            for i in range(0, 4):
                                with gr.Row():
                                    axis_count += 1
                                    row_mode = gr.Dropdown(value="", label=f"Axis {axis_count} Mode", choices=[" "] + [x.name for x in core.valid_modes.values()])
                                    row_value = gr.Textbox(label=f"Axis {axis_count} Value", lines=1)
                                    fill_row_button = ui_components.ToolButton(value=fill_values_symbol, visible=False)
                                    def fill_axis(mode_name):
                                        core.clear_caches()
                                        mode = core.valid_modes.get(clean_mode(mode_name))
                                        if mode is None:
                                            return gr.update()
                                        elif mode.type == "boolean":
                                            return "true, false"
                                        elif mode.valid_list is not None:
                                            return ", ".join(list(mode.valid_list()))
                                        raise RuntimeError(f"Can't fill axis for {mode_name}")
                                    fill_row_button.click(fn=fill_axis, inputs=[row_mode], outputs=[row_value])
                                    def on_axis_change(mode_name, out_file):
                                        mode = core.valid_modes.get(clean_mode(mode_name))
                                        button_update = gr.Button.update(visible=mode is not None and (mode.valid_list is not None or mode.type == "boolean"))
                                        (out_file_update, info_update) = update_page_url(out_file, "Create in UI")
                                        return [button_update, out_file_update, info_update]
                                    row_mode.change(fn=on_axis_change, inputs=[row_mode, output_file_path], outputs=[fill_row_button, output_file_path, page_will_be])
                                    manual_axes += list([row_mode, row_value])
                                    rows.append(row_mode)
                            sets.append([group_obj, rows])
        for group in range(0, 3):
            row_mode = sets[group][1][3]
            group_obj = sets[group + 1][0]
            next_rows = sets[group + 1][1]
            def make_vis(prior, r1, r2, r3, r4):
                return gr.Group.update(visible=(prior+r1+r2+r3+r4).strip() != "")
            row_mode.change(fn=make_vis, inputs=[row_mode] + next_rows, outputs=[group_obj])
        gr.HTML('<span style="opacity:0.5;">(More input rows will be automatically added after you select modes above.)</span>')
        grid_file.change(
            fn=lambda x: {"visible": x == "Create in UI", "__type__": "update"},
            inputs=[grid_file],
            outputs=[manual_group],
            show_progress = False)
        output_file_path.change(fn=update_page_url_single, inputs=[output_file_path, grid_file], outputs=[page_will_be])
        grid_file.change(fn=update_page_url, inputs=[output_file_path, grid_file], outputs=[output_file_path, page_will_be])
        with gr.Row():
            do_overwrite = gr.Checkbox(value=False, label="Overwrite existing images (for updating grids)")
            dry_run = gr.Checkbox(value=False, label="Do a dry run to validate your grid file")
            fast_skip = gr.Checkbox(value=False, label="Use more-performant skipping")
            skip_invalid = gr.Checkbox(value=False, label="Skip invalid entries")
        with gr.Row():
            generate_page = gr.Checkbox(value=True, label="Generate infinite-grid webviewer page")
            validate_replace = gr.Checkbox(value=True, label="Validate PromptReplace input")
            publish_gen_metadata = gr.Checkbox(value=True, label="Publish full generation metadata for viewing on-page")
        return [do_overwrite, generate_page, dry_run, validate_replace, publish_gen_metadata, grid_file, fast_skip, output_file_path, skip_invalid] + manual_axes

    def run(self, p, do_overwrite, generate_page, dry_run, validate_replace, publish_gen_metadata, grid_file, fast_skip, output_file_path, skip_invalid, *manual_axes):
        core.clear_caches()
        try_init()
        # Clean up default params
        p = copy(p)
        p.n_iter = 1
        p.batch_size = 1
        p.do_not_save_samples = True
        p.do_not_save_grid = True
        p.seed = processing.get_fixed_seed(p.seed)
        # Store extra variable
        Script.VALIDATE_REPLACE = validate_replace
        # Validate to avoid abuse
        if '..' in grid_file or grid_file == "":
            raise RuntimeError(f"Unacceptable filename '{grid_file}'")
        if '..' in output_file_path:
            raise RuntimeError(f"Unacceptable alt file path '{output_file_path}'")
        if grid_file == "Create in UI":
            if output_file_path is None or output_file_path == "":
                raise RuntimeError(f"Must specify the output file path")
            manual_axes = list(manual_axes)
        else:
            manual_axes = None
        with SettingsFixer():
            result = core.run_grid_gen(p, grid_file, p.outpath_grids, output_file_path, do_overwrite, fast_skip, generate_page, publish_gen_metadata, dry_run, manual_axes, skip_invalid=skip_invalid)
        if result is None:
            return Processed(p, list())
        return result
