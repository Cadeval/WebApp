# Minimal makefile for Sphinx documentation
#

# You can set these variables from the command line, and also
# from the environment for the first two.
SPHINXOPTS    ?=
SPHINXBUILD   ?= sphinx-build
SOURCEDIR     = .
BUILDDIR      = data/docs

# Put it first so that "make" without argument is like "make help".
help:
	@$(SPHINXBUILD) -M help "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

.PHONY: help run migrate Makefile

# Catch-all target: route all unknown targets to Sphinx using the new
# "make mode" option.  $(O) is meant as a shortcut for $(SPHINXOPTS).
%: Makefile
	@$(SPHINXBUILD) -M $@ "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

run:
	# gunicorn cadevil.asgi:application -k uvicorn.workers.UvicornWorker 127.0.0.1:8000
	# uvicorn --reload --workers 3 cadevil.asgi:application --lifespan off --host 127.0.0.1 --port 8000
	#python manage.py runserver_plus --threaded 127.0.0.1:8000
	 python manage.py runserver 127.0.0.1:8000

migrate:
	python manage.py makemigrations
	python manage.py migrate --run-syncdb

flush:
	python manage.py flush

superuser:
	python manage.py createsuperuser
