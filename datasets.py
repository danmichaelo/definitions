# -*- coding: UTF-8 -*-
from __future__ import unicode_literals, print_function
import os
import re
from rdflib import Graph, ConjunctiveGraph, RDF, Namespace
from lxltools.datacompiler import Compiler
from lxltools.contextmaker import DEFAULT_NS_PREF_ORDER, make_context, add_overlay


# TODO:
# * Explicitly link each record to it's parent dataset record unless we have an
#   ldp:IndirectContainer (we now have *some* defined for terms using inScheme).
# * Explicitly link each record to its logical source (or just the parent dataset record?)
#   (derivativeOf <github.com/libris/definitions/**/.ttl> + generationProcess <definitions>?)

NS_PREF_ORDER = DEFAULT_NS_PREF_ORDER + ['ldp']

SCRA = Namespace("http://purl.org/net/schemarama#")

BASE = "https://id.kb.se/"

SCRIPT_DIR = os.path.dirname(__file__) or '.'


def decorate(items, template):
    def decorator(item):
        for k, tplt in template.items():
            item[k] = tplt.format(**item)
        return item
    return list(map(decorator, items))


def to_camel_case(label):
    return "".join((s[0].upper() if i else s[0].lower()) + s[1:]
            for (i, s) in enumerate(re.split(r'[\s,.-]', label)) if s)


def _get_repo_version():
    try:
        with os.popen(
                '(cd {} && git describe --tags)'.format(SCRIPT_DIR)
                ) as pipe:
            return pipe.read().rstrip()
    except:
        return None


compiler = Compiler(base_dir=SCRIPT_DIR,
                    dataset_id=BASE + 'definitions',
                    context='sys/context/base.jsonld',
                    record_thing_link='mainEntity',
                    system_base_iri="",
                    union='definitions.jsonld.lines')


def _insert_record(graph, created_ms):
    entity = graph[0]
    record = {'@type': 'Record'}
    record[compiler.record_thing_link] = {'@id': entity['@id']}
    graph.insert(0, record)
    record['@id'] = compiler.generate_record_id(created_ms, entity['@id'])


#@compiler.dataset
#def datasets():
#    graph = Graph().parse(compiler.path('source/index.ttl'), format='turtle')
#    return to_jsonld(graph, (None, None), { "@language": "sv"})


# NOTE: this step is currently part of the source maintenance, used to sync
# with "unstable" marcframe mappings. I plan to inverse parts of this flow
# to generate token-maps (used by marcframe processors) from these vocab
# and enum sources instead.
#prep_vocab_data():
#    python scripts/vocab-from-marcframe.py
#           ext-libris/src/main/resources/marcframe.json build/vocab.ttl
#           > build/vocab-generated-source-1.ttl

@compiler.handler
def vocab():
    vocab_base = BASE + 'vocab/'

    graph = compiler.construct(sources=[
            {
                'source': Graph().parse(str(compiler.path('source/vocab/bf-map.ttl')), format='turtle'),
                'dataset': '?'
            },
            {"source": "http://id.loc.gov/ontologies/bibframe/"}
        ],
        query="source/vocab/bf-to-kbv-base.rq")

    for part in compiler.path('source/vocab').glob('**/*.ttl'):
        graph.parse(str(part), format='turtle')

    #cg = ConjunctiveGraph()
    #cg.parse(str(compiler.path('source/vocab/display.jsonld')), format='json-ld')
    #graph += cg

    graph.update(compiler.path('source/vocab/update.rq').read_text('utf-8'))
    graph.update(compiler.path('source/vocab/updateResource.rq').read_text('utf-8'))

    rq = compiler.path('source/vocab/construct-enum-restrictions.rq').read_text('utf-8')
    graph += Graph().query(rq).graph

    rq = compiler.path('source/vocab/check-bases.rq').read_text('utf-8')
    checks = graph.query(rq).graph
    for check, msg in checks.subject_objects(SCRA.message):
        print("{}: {}".format(
            graph.qname(checks.value(check, RDF.type)).split(':')[-1],
            msg.format(*[imp.n3() for imp in
                checks.collection(checks.value(check, SCRA.implicated))])
            ))

    for part in compiler.path('source/marc').glob('**/*.ttl'):
        graph.parse(str(part), format='turtle')

    graph.parse(str(compiler.path('source/swepub/vocab.ttl')), format='turtle')

    # Clean up generated prefixes
    preferred = {}
    defaulted = {}
    for pfx, uri in graph.store.namespaces():
        if pfx.startswith('default'):
            defaulted[uri] = pfx
        else:
            preferred[uri] = pfx
    for default_pfx, uri in defaulted.items():
        if uri in preferred:
            graph.namespace_manager.bind(preferred[uri], uri, override=True)

    data = compiler.to_jsonld(graph)
    del data['@context']

    # Put /vocab/* first (ensures that /marc/* comes after)
    data['@graph'] = sorted(data['@graph'], key=lambda node:
            (not node.get('@id', '').startswith(vocab_base), node.get('@id')))

    vocab_created_ms = compiler.ztime_to_millis("2014-01-01T00:00:00.000Z")
    _insert_record(data['@graph'], vocab_created_ms)
    vocab_node = data['@graph'][1]
    version = _get_repo_version()
    if version:
        vocab_node['version'] = version

    lib_context = make_context(graph, vocab_base, NS_PREF_ORDER)
    add_overlay(lib_context, compiler.load_json('sys/context/base.jsonld'))
    add_overlay(lib_context, compiler.load_json('source/vocab-overlay.jsonld'))
    lib_context['@graph'] = [{'@id': BASE + 'vocab/context'}]
    _insert_record(lib_context['@graph'], vocab_created_ms)

    display = compiler.load_json('source/vocab/display.jsonld')
    _insert_record(display['@graph'], vocab_created_ms)

    compiler.write(data, "vocab")
    compiler.write(lib_context, 'vocab/context')
    compiler.write(display, 'vocab/display')


@compiler.dataset
def enums():
    graph = Graph()
    rq = compiler.path('source/marc/construct-enums.rq').read_text('utf-8')
    graph += Graph().query(rq).graph

    return "/marc/", "2014-01-23T11:34:17.981Z", graph


@compiler.dataset
def rdaterms():
    # NOTE: see also examples/mappings/rda-bf2-types.ttl for possibiliy of
    # extending our type system (instead).
    graph = compiler.construct(sources=[
            {
                "source": list(compiler.read_csv('source/rdamap.tsv')),
                "context": "source/rdamap-context.jsonld"
            },

            {'source': 'http://rdaregistry.info/termList/RDAContentType.nt'},
            {'source': 'http://id.loc.gov/vocabulary/contentTypes'},

            {'source': 'http://rdaregistry.info/termList/RDAMediaType.nt'},
            {'source': 'http://id.loc.gov/vocabulary/mediaTypes'},

            {'source': 'http://rdaregistry.info/termList/RDACarrierType.nt'},
            {'source': 'http://id.loc.gov/vocabulary/carriers'},

            #{'source': 'http://rdaregistry.info/termList/ModeIssue'},
            #{'source': 'http://id.loc.gov/vocabulary/issuance.skos.rdf'},

        ],
        query="source/construct-rda-terms.rq")

    return "/term/rda/", "2018-05-16T08:18:01.337Z", graph


@compiler.dataset
def enumterms():
    graph = Graph().parse(str(compiler.path('source/kbv-enums.ttl')), format='turtle')

    return "/term/enum/", "2018-05-29T14:36:01.337Z", graph


@compiler.dataset
def swepubterms():
    graph = Graph()
    for part in compiler.path('source/swepub').glob('**/*.ttl'):
        if part.stem == 'vocab':
            continue
        graph.parse(str(part), format='turtle')

    return "/term/swepub/", "2018-05-29T14:36:01.337Z", graph


@compiler.dataset
def containers():
    graph = Graph().parse(str(compiler.path('source/containers.ttl')), format='turtle')

    return "/term/", "2019-07-11T15:04:17.964Z", graph


@compiler.dataset
def generators():
    graph = Graph().parse(str(compiler.path('source/generators.ttl')), format='turtle')

    return "/generator/", "2018-04-25T20:55:14.723Z", graph


@compiler.dataset
def schemes():
    graph = Graph().parse(str(compiler.path('source/schemes.ttl')), format='turtle')

    return "/", "2014-02-01T21:00:01.766Z", graph


@compiler.dataset
def relators():

    def relitem(item):
        item['@id'] = item.get('term') or to_camel_case(item['label_en'].strip())
        item['sameAs'] = {'@id': item['code']}
        return item

    graph = compiler.construct(sources=[
            {
                "source": list(map(relitem, compiler.read_csv('source/funktionskoder.tsv'))),
                "dataset": BASE + "dataset/relators",
                "context": ["sys/context/ns.jsonld", {
                    "@base": BASE + "relator/",
                    "@vocab": "https://id.kb.se/vocab/",
                    "code": "skos:notation",
                    "label_sv": {"@id": "skos:prefLabel", "@language": "sv"},
                    "label_en": {"@id": "skos:prefLabel", "@language": "en"},
                    "comment_sv": {"@id": "rdfs:comment", "@language": "sv"},
                    "term": "rdfs:label",
                    "sameAs": "owl:sameAs",
                    "domain": {"@id": "rdfs:domain", "@type": "@vocab"},
                    "rda_app_i_1_en": None,
                    "rda_app_i_2_en": None,
                    "rda_app_i_3_en": None
                }]
            },
            {
                "source": "http://id.loc.gov/vocabulary/relators"
            }
        ],
        query="source/construct-relators.rq")

    return "/relator/", "2014-02-01T17:29:12.378Z", graph


@compiler.dataset
def languages():
    loclanggraph = Graph()
    loclanggraph.parse(
            str(compiler.cache_url('http://id.loc.gov/vocabulary/iso639-1.nt')),
            format='nt')
    loclanggraph.parse(
            str(compiler.cache_url('http://id.loc.gov/vocabulary/iso639-2.nt')),
            format='nt')

    languages = decorate(compiler.read_csv('source/spraakkoder.tsv'),
        {"@id": BASE + "language/{code}"})

    graph = compiler.construct(sources=[
            {
                "source": languages,
                "dataset": BASE + "dataset/languages",
                "context": "source/table-context.jsonld"
            },
            {
                "source": loclanggraph,
                "dataset": "http://id.loc.gov/vocabulary/languages"
            }
        ],
        query="source/construct-languages.rq")

    return "/language/", "2014-08-01T09:56:51.110Z", graph


@compiler.dataset
def countries():
    graph = compiler.construct(sources=[
            {
                "source": decorate(compiler.read_csv('source/landskoder.tsv'),
                    {"@id": BASE + "country/{code}"}),
                "dataset": BASE + "dataset/countries",
                # TODO: fix rdflib_jsonld so urls in external contexts are loaded
                "context": "source/table-context.jsonld"
            },
            {
                "source": "http://id.loc.gov/vocabulary/countries"
            }
        ],
        query="source/construct-countries.rq")

    return "/country/", "2014-02-01T13:21:14.008Z", graph


@compiler.dataset
def nationalities():
    return "/nationality/", "2014-02-01T14:08:56.596Z", compiler.construct({
            "source": decorate(
                compiler.read_csv('source/nationalitetskoder.tsv'),
                {"@id": BASE + "nationality/{code}", "@type": 'Nationality'}),
            "context": [
                "sys/context/base.jsonld",
                {"label_sv": {"@id": "rdfs:label", "@language": "sv"}},
                {"label_en": {"@id": "rdfs:label", "@language": "en"}}
            ]
        })


@compiler.dataset
def docs():
    import markdown
    docs = []
    for fpath in compiler.path('source/doc').glob('**/*.mkd'):
        text = fpath.read_text('utf-8')
        html = markdown.markdown(text)
        doc_id = (str(fpath)
                  .replace(os.sep, '/')
                  .replace('source/', '')
                  .replace('.mkd', ''))
        doc_id, dot, lang = doc_id.partition('.')
        doc = {
            "@type": "Article",
            "@id": BASE + doc_id,
            "articleBody": html
        }
        h1end = html.find('</h1>')
        if h1end > -1:
            doc['title'] = html[len('<h1>'):h1end]
        if lang:
            doc['language'] = {"langTag": lang},
        docs.append(doc)

    return "/doc", "2016-04-15T16:43:38.072Z", {
        "@context": "../sys/context/base.jsonld",
        "@graph": docs
    }


if __name__ == '__main__':
    compiler.main()
