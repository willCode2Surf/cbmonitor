import json
import logging
from collections import defaultdict, OrderedDict

from couchbase import Couchbase
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db.utils import IntegrityError
from django.http import HttpResponse, Http404
from django.shortcuts import render_to_response
from django.views.decorators.cache import cache_page

from cbmonitor import forms
from cbmonitor import models
from cbmonitor.analyzer import Analyzer
from cbmonitor.helpers import SerieslyHandler
from cbmonitor.plotter import Plotter

logger = logging.getLogger(__name__)


def index(request):
    """Main interactive UI"""
    return render_to_response("interactive.jade")


def insight(request):
    """Insight UI"""
    return render_to_response("insight.jade")


def corr_matrix(request):
    """Interactive correlation matrix"""
    snapshot = request.GET.get("snapshot")
    return render_to_response("corr.jade", {"snapshot": snapshot})


@cache_page()
def html_report(request):
    """Static HTML reports with PNG charts"""
    try:
        snapshots = parse_snapshots(request)
    except ObjectDoesNotExist:
        return HttpResponse("Wrong or missing snapshot", status=400)
    plotter = Plotter()
    plotter.plot(snapshots)

    id_from_url = lambda url: url.split("/")[2].split(".")[0]
    urls = [(id_from_url(url), title, url) for title, url in plotter.urls]

    if urls:
        return render_to_response("report.jade", {"urls": urls})
    else:
        return HttpResponse("No metrics found", status=400)


def parse_snapshots(request):
    snapshots = []
    if "all_data" in request.GET.getlist("snapshot"):
        for cluster in request.GET.getlist("cluster"):
            cluster = models.Cluster(name=cluster)
            snapshot = models.Snapshot(name=cluster.name, cluster=cluster)
            snapshots.append(snapshot)
    else:
        for snapshot in request.GET.getlist("snapshot"):
            snapshot = models.Snapshot.objects.get(name=snapshot)
            snapshots.append(snapshot)
    return snapshots


class ValidationError(Exception):

    def __init__(self, form):
        self.error = {item[0]: item[1][0] for item in form.errors.items()}

    def __str__(self):
        return json.dumps(self.error)


def validation(method):
    def wrapper(*args, **kargs):
        try:
            response = method(*args, **kargs)
        except Http404, error:
            logger.warn(error)
            return HttpResponse(content=error, status=404)
        except ValidationError, error:
            logger.warn(error)
            return HttpResponse(content=error, status=400)
        except IntegrityError, error:
            logger.warn(error)
            return HttpResponse(content=error, status=400)
        else:
            return response or HttpResponse(content="Success")
    return wrapper


@validation
def add_cluster(request):
    form = forms.AddClusterForm(request.POST)
    if form.is_valid():
        form.save()
    else:
        raise ValidationError(form)


@validation
def add_server(request):
    form = forms.AddServerForm(request.POST)
    if form.is_valid():
        form.save()
    else:
        raise ValidationError(form)


@validation
def add_bucket(request):
    form = forms.AddBucketForm(request.POST)
    if form.is_valid():
        form.save()
    else:
        raise ValidationError(form)


@validation
def delete_cluster(request):
    cluster = models.Cluster.objects.get(name=request.POST["name"])
    cluster.delete()


def get_clusters(request):
    clusters = [c.name for c in models.Cluster.objects.all()]
    content = json.dumps(sorted(clusters))
    return HttpResponse(content)


@validation
def get_servers(request):
    form = forms.GetServersForm(request.GET)
    if form.is_valid():
        try:
            cluster = models.Cluster.objects.get(name=request.GET["cluster"])
            servers = models.Server.objects.filter(cluster=cluster).values()
            servers = [s["address"] for s in servers]
        except ObjectDoesNotExist:
            servers = []
    else:
        servers = []
    content = json.dumps(sorted(servers))
    return HttpResponse(content)


@validation
def get_buckets(request):
    form = forms.GetBucketsForm(request.GET)
    if form.is_valid():
        try:
            cluster = models.Cluster.objects.get(name=request.GET["cluster"])
            buckets = models.Bucket.objects.filter(cluster=cluster).values()
            buckets = [b["name"] for b in buckets]
        except ObjectDoesNotExist:
            buckets = []
    else:
        buckets = []
    content = json.dumps(sorted(buckets))
    return HttpResponse(content)


@validation
def get_metrics(request):
    form = forms.GetMetrics(request.GET)

    if form.is_valid():
        try:
            observables = models.Observable.objects.filter(**form.params).values()
            observables = [{"name": o["name"], "collector": o["collector"]}
                           for o in observables]
        except ObjectDoesNotExist:
            observables = []
    else:
        observables = []
    content = json.dumps(sorted(observables))
    return HttpResponse(content)


@validation
def add_metric(request):
    form = forms.AddMetric(request.POST)
    if form.is_valid():
        observable = form.save(commit=False)
        observable.bucket = form.cleaned_data["bucket"]
        observable.server = form.cleaned_data["server"]
        observable.save()
    else:
        raise ValidationError(form)


@validation
def add_snapshot(request):
    form = forms.AddSnapshot(request.POST)
    if form.is_valid():
        form.save()
    else:
        raise ValidationError(form)


def get_snapshots(request):
    cluster = request.GET["cluster"]
    snapshots = models.Snapshot.objects.filter(cluster=cluster).values()
    snapshots = [snapshot["name"] for snapshot in snapshots]
    snapshots.insert(0, "all_data")
    content = json.dumps(snapshots)
    return HttpResponse(content)


@cache_page()
def get_corr_matrix(request):
    snapshots = parse_snapshots(request)
    analyzer = Analyzer()
    columns, corr_matrix = analyzer.corr(snapshots)
    content = json.dumps({"columns": columns, "matrix": corr_matrix})
    return HttpResponse(content)


def get_insight_defaults(request):
    cb = Couchbase.connect(bucket="exp_defaults", **settings.COUCHBASE_SERVER)
    defaults = [
        row.value for row in cb.query("exp_defaults", "all", stale=False)
    ]
    content = json.dumps(defaults)
    return HttpResponse(content)


def get_default_inputs(insight):
    cb = Couchbase.connect(bucket="exp_defaults", **settings.COUCHBASE_SERVER)
    return cb.get(insight).value["inputs"]


def get_insight_options(request):
    insight = request.GET['insight']

    defaults = get_default_inputs(insight)
    data = {k: {v} for k, v in defaults.items()}

    cb = Couchbase.connect(bucket="experiments", **settings.COUCHBASE_SERVER)
    for row in cb.query("experiments", "experiments_by_name", key=insight, stale=False):
        for _input, value in row.value["inputs"].items():
            data[_input].add(value)

    options = []
    for _input, values in data.items():
        values = sorted(values)
        if len(values) > 1:
            values += ["Vary by", "As X-axis"]
        options.append({"title": _input, "options": values})

    content = json.dumps(options)
    return HttpResponse(content)


def get_insight_data(request):
    insight = request.GET["insight"]
    abscissa = request.GET["abscissa"]
    vary_by = request.GET.get("vary_by")
    inputs = json.loads(request.GET["inputs"])
    inputs.pop(abscissa)
    if vary_by:
        inputs.pop(vary_by)
    defaults = get_default_inputs(insight)

    cb = Couchbase.connect(bucket="experiments", **settings.COUCHBASE_SERVER)

    data = defaultdict(list)
    for row in cb.query("experiments", "experiments_by_name", key=insight, stale=False):
        value = row.value
        value_inputs = dict(defaults, **value["inputs"])
        if dict(value_inputs, **inputs) == value_inputs:
            key = value["inputs"].get(vary_by, defaults.get(vary_by))
            data[key].append((value_inputs[abscissa], value["value"]))
    for k, v in data.items():
        v.sort(key=lambda xy: xy[0])
    data = OrderedDict(sorted(data.items()))

    content = json.dumps(data)
    return HttpResponse(content)


def seriesly_proxy(request):
    sh = SerieslyHandler()
    db_name = sh.build_dbname(
        cluster=request.GET["cluster"],
        server=request.GET.get("server"),
        bucket=request.GET.get("bucket"),
        collector=request.GET.get("collector"),
    )
    data = sh.query_raw_data(db_name, name=request.GET["name"])

    content = json.dumps(data)
    return HttpResponse(content)
