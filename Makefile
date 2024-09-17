# Minimal makefile
#

debug:
	pypy manage.py runserver 0.0.0.0:8000

run:
	uvicorn --workers 4 cadevil.asgi:application --lifespan off --host 127.0.0.1 --port 8000

testrun:
	gunicorn cadevil.asgi:application -k uvicorn.workers.UvicornWorker --reload --preload --threads 8 --reuse-port -b 127.0.0.1:8000

migrate:
	pypy manage.py makemigrations
	pypy manage.py migrate --run-syncdb

flush:
	pypy manage.py flush

superuser:
	pypy manage.py createsuperuser