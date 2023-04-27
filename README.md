ECS :: Scheduled tasks
======================

Purpose
-------
This script is designed to build scheduled ecs tasks for running on an ecs cluster in AWS. 

Use
---
> There are no arguments currently supported, this expects a file `ecs.tasks.yml` in the current working directory, which defines the current ecs tasks to run. 
```
./ecs.task-deploy.py
```