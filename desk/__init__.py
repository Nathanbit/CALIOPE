"""CALIOPE desk: the person in the middle between forecasting agents and an exchange.

Agents propose orders; every order must come from a sealed forecast, pass the
risk limits of ``desk/policy.json`` and be approved (by the owner at a
terminal, or automatically only for authors that beat the rivals). Every
step is sealed in a hash-chained orders ledger before anything is sent.
"""
