FROM public.ecr.aws/lambda/python:3.12

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY src/ ${LAMBDA_TASK_ROOT}/src/
COPY cloud/lambda_ingest_handler.py cloud/lambda_predict_handler.py cloud/lambda_train_handler.py ${LAMBDA_TASK_ROOT}/

# Overridden per-function by the CMD set in template.yaml (ImageConfig.Command)
CMD ["lambda_ingest_handler.handler"]
