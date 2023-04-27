#!/usr/bin/env python

#
# Testing
#
# ./bin/ecs.task-deploy.py
#
# @deps
#   * boto3 (`pip install boto3`)
#   * yaml (`pip install yaml`)
#   * deepmerge (`pip install deepmerge`)
#
# @todo
#   * Support args passed in.
#   * Support custom yaml path for configuration.
#   * Think harder about improving execution call.
#   * Add logging / output.
#   * Add secondary target to send notifications to sns, log group, for possible tracking help.
#   * Add prompt support for confirmation before applying changes, provide support to auto accept as option.
#
import sys
import boto3
import json
import yaml
import copy
from deepmerge import always_merger

class EcsEventTask:
    """ Represent the combined rule+target implementation of an ECS Scheduled task. """
    config  = {}
    rule    = {}
    target  = {}
    client  = {}
    state   = "DISABLED"

    def __str__(self):
        output = '[config]::[{}]'.format(self.config)
        output += '::[rule]::[{}]'.format(self.rule)
        output += '::[target]::[{}]'.format(self.target)
        return output

    def __init__(self, config):
        self.config = config
        self.client = EcsEventTasksDeploy()
        self.state  = config['state'].upper() if 'state' in config else self.state
        self.name   = config['name'].upper() if 'name' in config else "default"
        self.rule = {
            'Name':                 config['name']          if 'name'           in config else None,
            'ScheduleExpression':   config['cron']          if 'cron'           in config else None,
            'Description':          config['description']   if 'description'    in config else None,
            'State':                'DISABLED' if self.state == 'DELETED' else self.state,
            'EventBusName':         config['event_bus']     if 'event_bus'      in config else None,
            'Tags':                 []
        }
        self.target = {
            'Id': config['name']             if 'name'           in config else None,
            'Arn': config['cluster_arn']      if 'cluster_arn'    in config else None,
            'RoleArn': config['role_arn']         if 'role_arn'       in config else None,
            'EcsParameters': config['ecs_parameters']   if 'ecs_parameters' in config else self.getTargetEcsParams(),
            'Input': config['input'] if 'input' in config else json.dumps(self.getTargetInput())
        }



    def getTargetInput(self):
        return {
            "containerOverrides": [{
                "name": self.config['task_group'] if 'task_group' in self.config else None,
                "environment": [{'name': key, 'value':self.config['environment_vars'][key]}  for key in self.config['environment_vars']]
            }]
        }

    def getTargetEcsParams(self):
        """
        @todo pull down latest task def version number to set if not in config.
        :return:
        """
        return {
            "TaskDefinitionArn": self.getLatestTaskDefinition(),
            "TaskCount": self.config['task_count'] if 'task_count' in self.config else 1,
            "LaunchType": self.config['launch_type'] if 'launch_type' in self.config else "EC2",
            "Group": self.config['task_group'] if 'task_group' in self.config else ""
        }

    def getLatestTaskDefinition(self):
        task_def_family = self.config['task_definition_family'] if 'task_definition_family' in self.config else None
        task_definition = self.config['task_definition'] if 'task_definition' in self.config else None

        if task_definition:
            return task_definition
        elif task_def_family:
            return self.client.get_latest_task_definition(task_def_family)


    def save(self):
        """Saves a rule and creates it's targets."""
        print('save()>>><<<<[{}]::[{}]').format(self.state, self.rule['Name'])
        self.rule['arn'] = self.client.put_rule(self.rule)
        self.client.put_targets({
                "Rule":         self.rule['Name'],
                "EventBusName": self.rule['EventBusName'],
                "Targets":      [dict(self.target)]
        })

    def remove(self):
        """ Removes a Rule and it's targets"""
        print('remove()>>><<<<[{}]::[{}]').format(self.state, self.rule['Name'])
        self.client.delete_rule(self.rule)

class EcsEventTasks:
    """
    Used as the main controller for this script by loading the yaml configuration,
    """
    config                  = {}
    config_path             = "ecs.tasks.yml"
    event_tasks             = []
    default_task_key        = "tasks"
    default_environment_key = "Environment"

    def __init__(self, path = None):
        self.loadConfig(path)

    def __str__(self):
        output = '[default_path]::[{}]'.format(self.config_path)
        output += '::[config]::[{}]'.format(self.config)
        for task in self.event_tasks:
            output += '::[event_task]::[{}]'.format(task)
        return output

    def loadConfig(self, path = None):
        """Load yaml config file """
        self.config_path = path if path else self.config_path
        with open(self.config_path, 'r') as stream:
            try:
                self.config = yaml.safe_load(stream)
            except yaml.YAMLError as exc:
                print(exc)

    def execute(self):
        self.buildEventTasks()
        self.deployEventTasks()

    def deployEventTasks(self):
        """Depending on the `state` defined in the task configuration, we either delete the task or update it."""

        for event_task in self.event_tasks:
            if event_task.state.lower() == "deleted":
                print('[state]::[remove()]::[{}]::[{}]').format(event_task.name, event_task.state.lower())
                event_task.remove()
            else:
                print('[state]::[save()]::[{}]::[{}]').format(event_task.name, event_task.state.lower())
                event_task.save()

    def buildEventTasks(self):
        """
        Used to build a list of EcsEventTask objects, which can be managed.
        We map a tasks `environment` value to the matching environment value in the global configuration.
        If you define a global configuration key in a task, it will override the global configuration for that one task.
        :return:
        """
        tasks       = self.config[self.default_task_key] if self.default_task_key in self.config else []
        merged_task = {}
        for task in tasks:
            config = self.config['environment'][task['environment_vars'][self.default_environment_key]]

            # reverse order in merge allows task config to override global config.
            if 'environment' in self.config and \
                    'environment_vars' in task and self.default_environment_key in task['environment_vars'] and \
                        task['environment_vars'][self.default_environment_key] in self.config['environment']:
                            merged_task = always_merger.merge(config.copy(), task.copy())

            self.event_tasks.append(EcsEventTask(merged_task))

class EcsEventTasksDeploy:
    """AWS Events Client used to abstract operations for CloudWatch Events"""
    client      = {}
    clients = {}

    def get_client(self, name):
        if name in self.clients:
            return self.clients[name]
        else:
            client = boto3.client(name)
            self.set_client(name, client)
            return client

    def set_client(self, name, client):
        self.client[name]   = client

    def __init__(self):
        print('---------------------------------------------------')

    def get_latest_task_definition(self, task_arn):
        response = ""
        task_def = ""
        try:
            response = self.get_client('ecs').list_task_definitions(
                familyPrefix    = task_arn,
                maxResults = 1,
                status  = 'ACTIVE',
                sort = 'DESC'
            )
        except:
            e = sys.exc_info()[0]
            print("[failed]::[get_latest_task_definition]::[task_arn]::[{}]::[response]::[{}]::[error]::[{}]").format(task_arn, response, e)
            raise

        if 'taskDefinitionArns' in response and len(response['taskDefinitionArns']) > 0:
            task_def = response['taskDefinitionArns'][0]

        return task_def



    def delete_rule(self, rule):
        """
        Delete rule in Cloud Watch Events.
        delete_rule(rule) called prior to delete rule; required order of operations from AWS.

        :param rule:
        :return: ResponseMetadata
        """
        response = ""
        try:
            self.delete_targets(rule)
            response = self.get_client('events').delete_rule(
                Name=rule['Name'],
                EventBusName=rule['EventBusName']
            )
        except:
            e = sys.exc_info()[0]
            print("[failed]::[delete_rule]::[rule]::[{}]::[response]::[{}]::[error]::[{}]").format(rule, response, e)
            raise


        return response

    def delete_targets(self, rule):
        """
        Delete the associated targets for a rule, must be done before deletion of a rule.
        :param rule:
        :return:
        """
        response = ""
        target_ids = self.get_targets_by(rule, 'Id')

        if not target_ids:
            return response

        try:
            response = self.get_client('events').remove_targets(
                Rule            = rule['Name'],
                EventBusName    = rule['EventBusName'],
                Ids             = target_ids
            )
        except:
            e = sys.exc_info()[0]
            print("[failed]::[delete_targets]::[rule]::[{}]::[response]::[{}]::[error]::[{}]").format(rule, response, e)
            raise

        return response


    def get_targets_by(self, rule, key='Id'):
        """
        Returns a list of target values that match the provided key.
        :param rule:
        :param key:
        :return:
        """
        targets = self.get_targets(rule)
        results = [target[key] for target in targets if key in target]
        return results

    def get_targets(self, rule):
        """
        Returns a List of targets associated with the provided rule.
        :param rule:
        :return:
        """

        response = ""
        try:
            response = self.get_client('events').list_targets_by_rule(
                Rule            = rule['Name'],
                EventBusName    = rule['EventBusName'],
                Limit           = 100
            )
        except self.get_client('events').exceptions.ResourceNotFoundException:
            print("[not found]::[get_targets]::[rule]::[{}]::[response]").format(rule)
            pass
        except:
            e = sys.exc_info()[0]
            print("[failed]::[get_targets]::[rule]::[{}]::[response]::[{}]::[error]::[{}]").format(rule, response, e)
            raise

        return response['Targets'] if 'Targets' in response else []

    def put_rule(self, rule):
        """
        Create or Updates rule in Cloud Watch Events.
        :param rule:
        :return: RuleArn
        """
        print('[put_rule]::[{}]').format(rule)
        response = ""
        try:
            response = self.get_client('events').put_rule(**rule)
        except:
            e = sys.exc_info()[0]
            print("[failed]::[put_rule]::[rule]::[{}]::[response]::[{}]::[error]::[{}]").format(rule, response, e)
            raise
        return response['RuleArn'] if 'RuleArn' in response else ""

    def put_targets(self, target):
        """
        Create or Updates the targets for a matching (name) rule.
        :param target:
        :return:
        """
        print('[put_targets]::[{}]').format(target)
        response = ""
        try:
            response = self.get_client('events').put_targets(**target)
        except:
            e = sys.exc_info()[0]
            print("[failed]::[put_targets]:[response]::[{}]::[error]::[{}]").format(response, e)
            raise
        return response

EcsEventTasks("ecs.tasks.yml").execute()