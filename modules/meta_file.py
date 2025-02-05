from __future__ import annotations
import datetime
import json
import configparser
import logging
import re
import os
import sys
from io import StringIO

from enum import Enum

# from pydantic import validate_arguments

from etc import constants, logger_config
from modules import deploy_action as da
from modules import deploy_object as do
from modules import stages as s
from modules import workflow as wf
from modules import ibm_i_commands
from modules import deploy_version as dv
from modules.cmd_status import Status as Cmd_Status
from modules import meta_file_history as mfh



class StageNotReadyException(Exception):
  pass



class Meta_file_status(Enum):

  NEW = 'new'
  READY = 'ready'
  IN_PROCESS = 'in process'
  FAILED = 'failed'
  FINISHED = 'finished'
  CANCELED = 'canceled'
  




class Meta_File:

    """
    Meta_File describes a information of a deployment
    It's the main controller
    """

    def __init__(self, project:str=None, workflow_name :str=None, workflow=None, file_name=None, 
                object_list=None, create_time=None, update_time=None, status :Meta_file_status=Meta_file_status.NEW, 
                deploy_version : int=None, processed_stages: s.Stage_List_list=None, open_stages: s.Stage_List_list=None, 
                imported_from_dict=False):

      logging.debug(f"{sys.path=}")

      self.file_name = None
      self.processed_stages = None
      self.open_stages = None
      self.current_running_stage = None
      self.current_user = None
      self.status = None
      self.backup_deploy_lib = None
      self.main_deploy_lib = None
      self.remote_deploy_lib = None
      self.create_date = None
      self.create_time = None
      self.commit = None
      self.release_branch = None
      self.project = project
      self.file_name = file_name
      self.deploy_version = deploy_version
      self.object_list = object_list
      self.run_history = mfh.Meta_File_History_List_list()
      self.activate_history()

      self.set_status(status, False)
        
      self.update_time = update_time
      self.create_time = create_time

      if self.object_list == None:
        self.object_list = constants.C_OBJECT_LIST

      if self.create_time == None:
        self.create_time = str(datetime.datetime.now())

      self.create_date = re.sub(" .*", '', self.create_time)
        
      self.workflow = workflow
      if workflow_name is not None:
        self.workflow = wf.Workflow(name=workflow_name)
      
      if self.project is None:
        self.project = self.workflow.default_project

      # Only processed and started stages are here
      # Next stages will be taken from self.workflow.stages
      self.processed_stages = processed_stages
      if self.processed_stages is None:
        self.processed_stages = s.Stage_List_list()

      self.open_stages = open_stages
      if self.open_stages is None:
        self.open_stages = s.Stage_List_list()
        start_stage = s.Stage.get_stage_from_workflow(self.workflow, 'START')
        self.open_stages.append(start_stage)
        
      self.deploy_objects = do.Deploy_Object_List()

      if deploy_version == None:
        self.deploy_version = dv.Deploy_Version.get_next_deploy_version(project=self.project, status=self.status)

      self.release_branch = constants.C_GIT_BRANCH_RELEASE.replace('{deploy_version}', str(self.deploy_version)).replace('{project}', self.project)

      if self.file_name == None:
        self.file_name = constants.C_DEPLOY_META_FILE
      self.file_name = os.path.abspath(self.file_name.format(**self.__dict__))

      self.set_deploy_main_lib(f"d{str(self.deploy_version).zfill(9)}")
      self.set_deploy_backup_lib(f"b{str(self.deploy_version).zfill(9)}")
      self.remote_deploy_lib = f"r{str(self.deploy_version).zfill(9)}"

      if not imported_from_dict:

        dv.Deploy_Version.update_deploy_status(self.project, self.deploy_version, self.status, self.file_name, self.commit)
        self.import_objects_from_config_file()
        self.copy_object_actions_2_open_stages()

        self.write_meta_file(False)



    def activate_history(self):
      logging.debug(f"Aktivate history log for {self.file_name}")
      logging.debug(f"0. Number of histories: {len(self.run_history)}")

      stdout_new = StringIO()
      history = mfh.Meta_File_History(log=stdout_new)
      self.run_history.add_history(history)

      logging.debug(f"1. Number of histories: {len(self.run_history)}")


      hdl = logging.StreamHandler(stream=stdout_new)
      hdl.setFormatter(logging.root.handlers[0].formatter)
      logging.getLogger().addHandler(hdl)



    def set_status(self, status, update_meta_file=True):

      logging.debug(f"Set status to {status}")

      if type(status) == str:
        status = Meta_file_status(status)

      logging.debug(f"Update meta file: {update_meta_file}")

      if self.status == Meta_file_status.CANCELED:
        raise Exception("Deployment has been canceled already. It's not possible to change the status!")

      if update_meta_file and status is not Meta_file_status.NEW:
        logging.debug(f"Finished 1.0")
        dv.Deploy_Version.update_deploy_status(self.project, self.deploy_version, status, self.file_name, self.commit)
        logging.debug(f"Finished 1")
        self.status = status
        self.write_meta_file()

      self.status = status
      logging.debug(f"Finished meta file status set")




    def set_action_check(self, stage_id: int, action_id: int, check: bool, current_user: str) -> None:
      
      stage = self.open_stages.get_stage(stage_id)
      stage.actions.set_action_check(action_id, check)

      stage.processing_users.append({'user': current_user, 'timestamp' : str(datetime.datetime.now()), 'action' : s.Actions.SET_CHECK_ERROR})
      self.write_meta_file()




    def set_next_stage(self, from_stage: s.Stage):
      """Add next stages to open_stages list

      Args:
          from_stage (s.Stage): Current finished stage
      """

      next_stages = from_stage.get_next_stages_name()

      #commands = ibm_i_commands.IBM_i_commands(self)

      # 1. Remove the from_stage from current stages
      # 2. Add next stages to current stages
      # 3. Set global stages commands
      self.processed_stages.append(from_stage)
      self.open_stages.remove_stage(from_stage.id)

      for next_stage in next_stages:

        existing_stages = [*self.processed_stages.get_stages_by_name(next_stage), *self.open_stages.get_stages_by_name(next_stage)]

        logging.debug(f'{next_stage=}')
        # Check if already processed
        if len(existing_stages) > 0:
          for es in existing_stages:
            logging.debug(f"Stage ({next_stage}) already processed: {es.id}")
            from_stage.next_stage_ids.append(es.id)
            es.from_stage_id.append(from_stage.id)
          continue

        logging.debug(f"Register new stage for {next_stage}")
        next_stage_object = s.Stage.get_stage_from_workflow(self.workflow, next_stage)
        logging.debug(f"New stage id {next_stage_object.id}")
        
        from_stage.next_stage_ids.append(next_stage_object.id)
        next_stage_object.from_stage_id.append(from_stage.id)
        self.open_stages.append(next_stage_object)
        self.copy_object_actions_2_open_stages(next_stage_object.id)
      
      logging.debug(f'{self.open_stages.summary()=}')
      if len(self.open_stages) == 0:
        self.set_status(Meta_file_status.FINISHED)

      self.write_meta_file()

      


    def get_next_stages(self, from_stage: s.Stage) -> s.Stage_List_list:
      """Get's following stages objects from a stage
          This is used for workflow drawing in flowchart.py

      Args:
          from_stage (s.Stage): Basis stage object

      Returns:
          s.Stage_List_list: List of stages
      """

      next_stages = s.Stage_List_list()

      for next_id in from_stage.next_stage_ids:
        next_stages.append(self.get_stage_by_id(next_id))

      logging.debug(f"Next stages for {from_stage.name} ({from_stage.id}): {next_stages.get_all_ids}")
      if len(next_stages) > 0:
        return next_stages

      logging.debug(f"Next stages 2 for {from_stage.name}: {from_stage}")
      return from_stage.next_stages




    def get_stages_needs_2_get_finished(self, stage: Stage) -> []:
      if stage.after_stages_finished is None or len(stage.after_stages_finished) == 0:
        return []

      waiting_for_stages = []

      for stage_name in stage.after_stages_finished:
        if stage_name not in self.processed_stages.get_all_names():
          waiting_for_stages.append(stage_name)
      
      return waiting_for_stages




    def run_current_stages(self) -> None:

      for open_stage_id in self.open_stages.get_all_ids():
        self.run_current_stage(open_stage_id)



    def run_current_stage_as_thread(self, stage_id: int, processing_step: str=None, continue_run=True) -> threading.Thread:

      logging.debug(f"Start deployment check")
      self.check_deployment_ready_2_run(stage_id=stage_id, processing_step=processing_step)
      logging.debug(f"Deployment check successfully passed")

      import threading
      t = threading.Thread(target=self.run_current_stage, args=(stage_id, processing_step, continue_run))
      t.start()
      return t



    def check_deployment_ready_2_run(self, stage_id: int, processing_step: str=None):

      if self.status != Meta_file_status.READY:
        raise Exception(f"Meta file is not in status 'ready', but in status '{self.status.value}'!")
      
      runable_stage = self.open_stages.get_stage(id=stage_id)
      
      if runable_stage is None:
        e = Exception(f"Stage id '{stage_id}' is not available to run!")
        logging.exception(e, stack_info=True)
        raise e
  
      if processing_step is not None and processing_step not in runable_stage.processing_steps:
        e = Exception(f"Processing step '{processing_step}' is not defined in stage '{runable_stage.name}' (id {runable_stage.id}). Defined steps are: {runable_stage.processing_steps}")
        logging.exception(e, stack_info=True)
        self.write_meta_file()
        raise e

      waiting_for_stages = self.get_stages_needs_2_get_finished(runable_stage)
      if len(waiting_for_stages) > 0:
        e = StageNotReadyException(f"Stage {runable_stage.name} ({stage_id}) is still waiting for other stages to get finished: {waiting_for_stages}")
        logging.exception(e, stack_info=True)
        raise e

      dv.Deploy_Version.validate_deployment(self.project, self.deploy_version, Meta_file_status.IN_PROCESS)




    def run_current_stage(self, stage_id: int, processing_step: str=None, continue_run=True) -> None:
      """Run given stage

      Args:
          stage_id (int): Stage id
          processing_step (str, optional): Step of stage. Defaults to None.
              If None, all steps will be issued
          continue_run (bool, optional):
              True: Continiue from first step which has not been finished
              False: Run all steps from this stage, even if they already have been finished successful

      Raises:
          Exception: If a processing step was given, which is not in the step list of that stage
      """

      self.check_deployment_ready_2_run(stage_id=stage_id, processing_step=processing_step)

      try:
        self.set_status(Meta_file_status.IN_PROCESS)
      except Exception as err:
        logging.exception(err, stack_info=True)
        self.write_meta_file()
        raise err

      runable_stage = self.open_stages.get_stage(id=stage_id)

      cmd = ibm_i_commands.IBM_i_commands(self)
      
      logging.info(f"Run stage {runable_stage.name} (id {runable_stage.id}), {processing_step=}")

      self.current_running_stage = runable_stage

      try:
        cmd.run_commands(stage=runable_stage, processing_step=processing_step, continue_run=continue_run)
      except Exception as err:
        logging.exception(err, stack_info=True)
        self.set_status(Meta_file_status.FAILED)
        raise err

      logging.info(f"All actions completed '{runable_stage.name}'")
      logging.info(f"Set meta file status to '{Meta_file_status.READY}'")

      self.check_stage_finish(runable_stage)

      self.set_status(Meta_file_status.READY)

      self.check_deployment_finish()
      logging.info(f"Finished run of stage '{runable_stage.name}'")




    def check_stage_finish(self, stage: s.Stage) -> None:

      logging.debug("Check if stage has been finished")
      
      for action in stage.actions:

        if action.status not in [Cmd_Status.FINISHED, Cmd_Status.FAILED] or (action.status == Cmd_Status.FAILED and action.check_error == True):

          logging.info(f"Action {action.id} is in status '{action.status}'. So stage is not finished yet")
          # if stage is not completed, don't set the FINISHED status.
          return

      stage.status = Cmd_Status.FINISHED

      logging.info(f"Stage {stage.name} ({stage.id}) has been finished. Setting next stage(s) {stage.next_stages.get_all_names()}")
      self.set_next_stage(stage)




     #@validate_arguments
    def check_deployment_finish(self) -> None:

      if len(self.open_stages) == 0:
        logging.info(f"Deployment {self.file_name} has been finished.")
        self.set_status(Meta_file_status.FINISHED)



     #@validate_arguments
    def set_deploy_main_lib(self, library: str):
      self.main_deploy_lib = library.lower()



     #@validate_arguments
    def set_deploy_backup_lib(self, library: str):
      self.backup_deploy_lib = library.lower()



    
    def add_deploy_object(self, object: type[do.Deploy_Object]):

      self.deploy_objects.add_object(object)



    
    def is_backup_name_already_in_use(self, obj_lib: str, obj_name: str, backup_name: str, obj_type: str):
      """
      Parameters
      ----------
      obj_lib : str
          Library name from asking object
      obj_name : str
          Object name from asking object
      backup_name : str, optional
          Suggested backup name which needs to be checked for uniqueness  
      ----------
      """

      for lib in self.deploy_objects:
        for obj in self.deploy_objects[lib]:
          # Check if back-up name is already in use
          if obj['obj_type'] == obj_type and obj.get('backup_name', '') == backup_name:
            return True
          
          # Check if back-up name is already used as object name
          if (not (lib == obj_lib and 
                  obj['obj_type'] == obj_type and 
                  obj['obj_name'] == obj_name) and 
             obj['obj_type'] == obj_type and 
             obj['obj_name'] == backup_name):
            return True

      return False



    def set_deploy_objects(self, objects: []):

      for obj in objects:
        self.add_deploy_object(do.Deploy_Object(dict=obj))



    def get_stage_by_id(self, stage_id: int) -> s.Stage:

      if type(stage_id) == str:
        stage_id = int(stage_id)

      if stage_id in self.open_stages.get_all_ids():
        return self.open_stages.get_stage(stage_id)

      if stage_id in self.processed_stages.get_all_ids():
        return self.processed_stages.get_stage(stage_id)
      
      logging.error(f'No stage found with id {stage_id}. Existing: {self.open_stages.get_all_ids()=}, {self.processed_stages.get_all_ids()=}')



    def get_stages_by_name(self, stage: str) -> s.Stage:

      if stage in self.open_stages.get_all_names():
        return self.open_stages.get_stages_by_name(stage)

      if stage in self.processed_stages.get_all_names():
        return self.processed_stages.get_stages_by_name(stage)


    
    def get_actions(self, processing_step: str=None, stage_id: int=None, action_id: int=None, include_subactions: bool=True) -> []:

      list=[]

      if stage_id is None:
        raise Exception(f"Stage id is None")

      stage_obj = self.get_stage_by_id(stage_id)

      list=stage_obj.actions.get_actions(processing_step=processing_step, action_id=action_id, include_subactions=include_subactions)
      
      list = list + self.deploy_objects.get_actions(processing_step=processing_step, stage=stage_obj.name, action_id=action_id, include_subactions=include_subactions)
      
      return list



#    def get_next_open_action(self, processing_step: str=None, stage: str=None):
#      for action in self.get_actions(processing_step=processing_step, stage=stage):
#        if action.status == Cmd_Status.FINISHED or (action.status == Cmd_Status.FAILED and action.check_error == False):
#          continue
#        return action
      




    def load_json_file(file_name: str) -> Meta_File:

      logging.debug(f"Load meta file {file_name}")

      with open (file_name, "r") as file:
        meta_file_json=json.load(file)
        
        #workflow = wf.Workflow(name=meta_file_json['general']['workflow']['name'])
        workflow = wf.Workflow(dict=meta_file_json['general']['workflow'])
        meta_file = Meta_File(workflow=workflow,
                              project=meta_file_json['general']['project'],
                              deploy_version=meta_file_json['general']['deploy_version'],
                              status=meta_file_json['general']['status'],
                              file_name=f"{meta_file_json['general']['file_name']}",
                              create_time=meta_file_json['general']['create_time'],
                              update_time=meta_file_json['general']['update_time'],
                              processed_stages=s.Stage_List_list(workflow=workflow,iterable=meta_file_json['general']['processed_stages']),
                              open_stages=s.Stage_List_list(workflow=workflow,iterable=meta_file_json['general']['open_stages']),
                              object_list=meta_file_json['general']['object_list'],
                              imported_from_dict=True
                             )
        meta_file.commit=meta_file_json['general']['commit']
        meta_file.release_branch=meta_file_json['general']['release_branch']

        meta_file.set_deploy_objects(meta_file_json['objects'])
        meta_file.set_deploy_main_lib(meta_file_json['deploy_libs']['main_lib'])
        meta_file.set_deploy_backup_lib(meta_file_json['deploy_libs']['backup_lib'])
        meta_file.remote_deploy_lib = meta_file_json['deploy_libs']['remote_lib']
        #meta_file.actions.add_actions_from_list(meta_file_json['deploy_cmds'])
        
        meta_file.run_history.add_historys_from_list(meta_file_json['run_history'])

        #meta_file.write_meta_file()

        return meta_file
      
      raise Exception(f"Meta file {file_name} does not exist")



    def cancel_deployment(self):
      self.set_status(Meta_file_status.CANCELED)
      logging.info('Deployment has been canceled!')
      self.write_meta_file()


    # Load meta file based on its version number
    def load_version(project:str, version: int) -> Meta_File:

      deployment = dv.Deploy_Version.get_deployment(project, version)

      if deployment is None or 'meta_file' not in deployment:
        raise Exception(f"Couldn't find deployment version {version}: {project=}, {deployment=}")

      return Meta_File.load_json_file(deployment['meta_file'])



    def get_all_data_as_dict(self) -> {}:

      dict = {}
      dict['general'] = {'workflow':        self.workflow.get_dict(),
                         'project':         self.project,
                         'deploy_version':  self.deploy_version,
                         'file_name':       self.file_name,
                         'commit':          self.commit,
                         'release_branch':  self.release_branch,
                         'create_time':     self.create_time,
                         'update_time':     self.update_time,
                         'status':          self.status.value,
                         'object_list':     self.object_list,
                         'processed_stages':  self.processed_stages.get_dict(),
                         'open_stages':  self.open_stages.get_dict(),
                        }
      dict['deploy_libs'] = {'main_lib':    self.main_deploy_lib,
                             'remote_lib':  self.remote_deploy_lib,
                             'backup_lib':  self.backup_deploy_lib,
                            }
      #dict['deploy_cmds'] = self.get_actions_as_dict()
      dict['objects'] = self.deploy_objects.get_objectjs_as_dict()
      dict['run_history'] = self.run_history.get_list()
      logging.debug(f"Number of histories: {len(self.run_history)}")

      return dict



    def __eq__(self, o):
      s=self
      result = s.deploy_objects == o.deploy_objects
      result = s.open_stages == o.open_stages
      result = s.processed_stages == o.processed_stages

      if (s.status, s.project, s.deploy_version, s.update_time, s.create_time, s.file_name, s.object_list, s.commit, s.release_branch, s.processed_stages, s.deploy_objects, s.backup_deploy_lib, s.main_deploy_lib) == \
         (o.status, o.project, o.deploy_version, o.update_time, o.create_time, o.file_name, o.object_list, s.commit, s.release_branch, o.processed_stages, o.deploy_objects, o.backup_deploy_lib, o.main_deploy_lib):
        return True
      return False



    
    def write_meta_file(self, update_time: bool=True):

      if update_time:
        self.update_time = str(datetime.datetime.now())

      file_dir = os.path.dirname(os.path.realpath(self.file_name))
      if not os.path.isdir(file_dir):
        os.makedirs(file_dir)

      logging.debug(f"Save meta file to {self.file_name}")
      
      with open(self.file_name, 'w') as file:
        json.dump(self.get_all_data_as_dict(), file, default=str, indent=4)



    
    def add_object_from_meta_structure(self, objects: [], object_type: str):

      for obj in objects:

          if '/' not in obj:
              continue

          obj = obj.split('/')
          deploy_obj = do.Deploy_Object(lib=obj[0], name=obj[1], type=object_type)

          self.add_deploy_object(deploy_obj)



    
    def import_objects_from_config_file_old(self, config_file: str):

      object_config = configparser.ConfigParser()
      object_config.read(config_file, encoding='UTF-8')

      obj_list = dict(object_config.items('OBJECTS'))
      for obj_type in obj_list:
          self.add_object_from_meta_structure(obj_list[obj_type].split(' '), obj_type)

      #self.write_meta_file()


    
    def import_objects_from_config_file(self):
      """
      1. Import object list
         Objects to be deployed
      
      2. Import object specific actions
         These actions will be added in set_next_stage function
      """
      '''
      {constant:prod_obj} | {lib on production system} | {lib on source system} | {object to be saved}
      prod_obj|prouzalib|prouzadev|testlog_test|prouzalib/qrpglesrc/testlog_test.rpgle.pgm
      '''

      self.deploy_objects = do.Deploy_Object_List()

      if self.object_list is None:
        return
      if not os.path.exists(self.object_list):
        logging.warning(f"Object list {self.object_list} does not exist")
        return

      file_path = self.object_list

      logging.debug(f"Abs. file: {os.path.abspath(file_path)}")
      logging.debug(f"File: {file_path}")

      with open(file_path, "r") as file:
        for line in file:
          logging.debug(f"Import object: {line}")
          tmp = line.lower().rstrip('\r\n').rstrip('\n').split('|')
          logging.debug(f"{tmp=}")
          prod_lib = tmp[1]
          dev_lib = tmp[2]
          target_obj = tmp[3]
          obj_type = tmp[4]
          obj_attr = tmp[5]
          logging.debug(f"{prod_lib=}")
          logging.debug(f"{dev_lib=}")
          logging.debug(f"{target_obj=}")

          obj = do.Deploy_Object(lib=dev_lib, prod_lib=prod_lib, name=target_obj, type=obj_type, attribute=obj_attr)
          self.add_deploy_object(obj)
      
      self.load_actions_from_json(constants.C_OBJECT_COMMANDS)
      self.write_meta_file()

      #config_file_name = os.path.basename(self.object_list)
      #path = os.path.dirname(os.path.realpath(self.file_name))

      #os.rename(self.object_list, f"{path}/{self.deploy_version}_{config_file_name}")



    
    def load_actions_from_json(self, file: str):
      obj_cmds = []

      with open(file, "r") as file:
        obj_cmds = json.load(file)

      for oc in obj_cmds:
        self.deploy_objects.add_object_action_from_dict(dict=oc, workflow=self.workflow)




    def copy_object_actions_2_open_stages(self, stage_id: int=None):
      """
      Add object actions to related open stages

      Args:
          stage_id (int, optional): Only that stage should get actions
      """

      open_stages = self.open_stages

      if stage_id is not None:
        open_stages = s.Stage_List_list()
        open_stages.append(self.open_stages.get_stage(stage_id))

      logging.info(f"Add object ({len(self.deploy_objects)}) actions to stages: {open_stages.get_all_names()}")

      for do in self.deploy_objects:
        
        for do_action in do.actions:
          
          for os in open_stages:

            if os.name == do_action.stage:
              copy_action = do_action.get_dict()
              copy_action['id'] = None
              os.actions.add_actions_from_dict(copy_action)
