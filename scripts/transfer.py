from __future__ import annotations
import os, logging

from etc import constants
from modules import meta_file as mf, stages as s
from modules import deploy_action as da
from modules import ibm_i_commands
from modules.cmd_status import Status as Cmd_Status
from modules.object_status import Status as Obj_Status


def set_cmd_transfer_to_target(meta_file: mf.Meta_File, stage_obj: s.Stage, action: da.Deploy_Action) -> None:
    """Transfer all SAVFs to the target system

    Args:
        stage (str): _description_
    Example:
        scp -rp /dir/deployment1 target_server:~/also-a-dir
    """

    actions = stage_obj.actions
    deployment_dir = os.path.dirname(os.path.realpath(meta_file.file_name))

    cmd = f"scp -rp {deployment_dir} {stage_obj.host}:{stage_obj.remote_dir}"
    actions.add_action_cmd(
        cmd=cmd,
        environment=da.Command_Type.PASE,
        processing_step=action.processing_step,
        stage=stage_obj.name,
        add_after=action
    )



def transfer_to_target(meta_file: mf.Meta_File, stage_obj: s.Stage, action: da.Deploy_Action) -> None:
    """Transfer all SAVFs to the target system

    Args:
        stage (str): _description_
    Example:
        scp -rp /dir/deployment1 target_server:~/also-a-dir
    """

    actions = stage_obj.actions
    deployment_dir = os.path.dirname(os.path.realpath(meta_file.file_name))
    meta_file.deploy_objects.set_objects_status(Obj_Status.IN_TRANSVER)

    cmd = ibm_i_commands.IBM_i_commands(meta_file)

    run_action = action.sub_actions.add_action(da.Deploy_Action(
        cmd=f"scp -rp {deployment_dir} {stage_obj.host}:{stage_obj.remote_dir}",
        environment=da.Command_Type.PASE,
        processing_step=action.processing_step,
        check_error=action.check_error, run_in_new_job=action.run_in_new_job,
        stage=stage_obj.name
    ))

    cmd.execute_action(stage=stage_obj, action=run_action)

    meta_file.deploy_objects.set_objects_status(Obj_Status.TRANSVERRED)
