"""Synthèses d'équipe ; critères identiques au modèle hebdomadaire fourni."""
from collections import defaultdict
from datetime import date, timedelta
from .parcelles_count import compter_parcelles


def primes_equipes(rows, cfg, weeks, today=None):
    today = today or date.today()
    groups = defaultdict(dict)
    for row in rows:
        agents = groups[(row['w'], row['d'], row['s'])]
        agent = agents.setdefault(row.get('code_agent') or row['a'], dict(ha=0, n=0, pt=0, rt=0))
        agent['ha'] += float(row['ha'])
        agent['n'] += 1
        agent['pt'] += row['p'] == 't'
        agent['rt'] += row['r'] == 't'
    result = {}
    for (week, dep, sup), agents in groups.items():
        values = list(agents.values())
        objective = float(cfg.get('obj', {}).get(dep, 150))
        threshold = float(cfg.get('seuil', 100))
        count = len(values)
        surface = sum(a['ha'] >= objective for a in values)
        pvcl = sum(a['pt'] == a['n'] for a in values)
        rdc = sum(a['rt'] == a['n'] for a in values)
        eligible = all(n / count * 100 >= threshold for n in (surface, pvcl, rdc))
        index = next(i for i, w in enumerate(weeks) if w['k'] == week)
        grace = (date.fromisoformat(weeks[index + 1]['e']) if index + 1 < len(weeks)
                 else date.fromisoformat(weeks[index]['e']) + timedelta(days=7))
        pending = surface / count * 100 >= threshold and today <= grace
        result[(week, dep, sup)] = dict(
            semaine=week, debut=weeks[index]['s'], fin=weeks[index]['e'], echeance=grace,
            statut='Éligible' if eligible else 'En attente' if pending else 'Non éligible',
            agents_primes=sum(a['ha'] >= objective and a['pt'] == a['n'] and a['rt'] == a['n'] for a in values),
            eligible=eligible, objectif=objective * count, effectif=count,
        )
    return result


def regrouper_equipes(rows, primes):
    groups = {}
    for row in rows:
        key = (row['d'], row['s'])
        team = groups.setdefault(key, dict(d=key[0], s=key[1], ha=0, n=0, pt=0, rt=0, pr=0, agents=set(), semaines=set(), parcelles=[]))
        team['ha'] += float(row['ha'])
        team['n'] += 1
        team['pt'] += row['p'] == 't'
        team['rt'] += row['r'] == 't'
        team['pr'] += row['p'] == 't' and row['r'] == 't'
        team['agents'].add(row.get('code_agent') or row['a'])
        team['parcelles'].append(row)
        team['semaines'].add(row['w'])
    for team in groups.values():
        team['effectif'] = len(team.pop('agents'))
        team['nb_parcelles'] = compter_parcelles(team.pop('parcelles'))
        team['primes'] = [primes[(w, team['d'], team['s'])] for w in sorted(team.pop('semaines'))]
    teams = sorted(groups.values(), key=lambda t: (t['d'], -t['ha'], t['s']))
    max_dep = {}
    for team in teams:
        max_dep[team['d']] = max(max_dep.get(team['d'], 0), round(team['ha'], 2))
    for team in teams:
        score = round(team['ha'], 2)
        team['meilleure_performance'] = score > 0 and score == max_dep[team['d']]
    return teams
