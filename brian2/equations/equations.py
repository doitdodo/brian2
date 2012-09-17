# ----------------------------------------------------------------------------------
# Copyright ENS, INRIA, CNRS
# Contributors: Romain Brette (brette@di.ens.fr) and Dan Goodman (goodman@di.ens.fr)
# 
# Brian is a computer program whose purpose is to simulate models
# of biological neural networks.
# 
# This software is governed by the CeCILL license under French law and
# abiding by the rules of distribution of free software.  You can  use, 
# modify and/ or redistribute the software under the terms of the CeCILL
# license as circulated by CEA, CNRS and INRIA at the following URL
# "http://www.cecill.info". 
# 
# As a counterpart to the access to the source code and  rights to copy,
# modify and redistribute granted by the license, users are provided only
# with a limited warranty  and the software's author,  the holder of the
# economic rights,  and the successive licensors  have only  limited
# liability. 
# 
# In this respect, the user's attention is drawn to the risks associated
# with loading,  using,  modifying and/or developing or reproducing the
# software by the user in light of its specific status of free software,
# that may mean  that it is complicated to manipulate,  and  that  also
# therefore means  that it is reserved for developers  and  experienced
# professionals having in-depth computer knowledge. Users are therefore
# encouraged to load and test the software's suitability as regards their
# requirements in conditions enabling the security of their systems and/or 
# data to be ensured and,  more generally, to use and operate it in the 
# same conditions as regards security. 
# 
# The fact that you are presently reading this means that you have had
# knowledge of the CeCILL license and that you accept its terms.
# ----------------------------------------------------------------------------------
# 

'''
Differential equations for Brian models.
'''
import keyword
import re
import string

from pyparsing import (Group, ZeroOrMore, OneOrMore, Optional, Word, CharsNotIn,
                       Combine, Suppress, restOfLine, LineEnd, ParseException)

from brian2.units.fundamentalunits import DimensionMismatchError
from brian2.units.allunits import second
from brian2.equations.unitcheck import get_unit_from_string
from brian2.equations.codestrings import CodeString
from brian2.utils.stringtools import word_substitute

__all__ = ['Equations']

# A dictionary mapping equation types to nice names for error messages
EQUATION_TYPE = {'parameter': 'parameter',
                 'diff_equation': 'differential equation',
                 'static_equation': 'static equation'}

# Units of the special variables that are always defined
UNITS_SPECIAL_VARS = {'t': second, 'dt': second, 'xi': second**-0.5}

# Definitions of equation structure for parsing with pyparsing
###############################################################################
# Basic Elements
###############################################################################

# identifiers like in C: can start with letter or underscore, then a
# combination of letters, numbers and underscores
# Note that the check_identifiers function later performs more checks, e.g.
# names starting with underscore should only be used internally
IDENTIFIER = Word(string.ascii_letters + '_',
                  string.ascii_letters + string.digits + '_').setResultsName('identifier')

# very broad definition here, expression will be analysed by sympy anyway
# allows for multi-line expressions, where each line can have comments
EXPRESSION = Combine(OneOrMore((CharsNotIn(':#\n') +
                                Suppress(Optional(LineEnd()))).ignore('#' + restOfLine)),
                     joinString=' ').setResultsName('expression')


# a unit
# very broad definition here, again. Whether this corresponds to a valid unit
# string will be checked later
UNIT = Word(string.ascii_letters + string.digits + '*/. ').setResultsName('unit')

# a single Flag (e.g. "const" or "event-driven")
FLAG = Word(string.ascii_letters + '_-')

# Flags are comma-separated and enclosed in parantheses: "(flag1, flag2)"
FLAGS = (Suppress('(') + FLAG + ZeroOrMore(Suppress(',') + FLAG) +
         Suppress(')')).setResultsName('flags')

###############################################################################
# Equations
###############################################################################
# Three types of equations
# Parameter:
# x : volt (flags)
PARAMETER = Group(IDENTIFIER + Suppress(':') + UNIT +
                  Optional(FLAGS)).setResultsName('parameter')

# Static equation:
# x = 2 * y : volt (flags)
STATIC_EQ = Group(IDENTIFIER + Suppress('=') + EXPRESSION + Suppress(':') +
                  UNIT + Optional(FLAGS)).setResultsName('static_equation')

# Differential equation
# dx/dt = -x / tau : volt
DIFFOP = (Suppress('d') + IDENTIFIER + Suppress('/') + Suppress('dt'))
DIFF_EQ = Group(DIFFOP + Suppress('=') + EXPRESSION + Suppress(':') + UNIT +
                Optional(FLAGS)).setResultsName('diff_equation')

# ignore comments
EQUATION = (PARAMETER | STATIC_EQ | DIFF_EQ).ignore('#' + restOfLine)
EQUATIONS = ZeroOrMore(EQUATION)


def check_identifier_basic(identifier):
    '''
    Check an identifier (usually resulting from an equation string provided by
    the user) for conformity with the rules:
    
        1. Only ASCII characters
        2. Starts with underscore or character, then mix of alphanumerical
           characters and underscore
        3. Is not a reserved keyword of Python
    
    Arguments:
    
    ``identifier``
        The string that should be checked
    
    The function raises a ``ValueError`` if the identifier does not conform to
    the above rules.
    '''
    
    # Check whether the identifier is parsed correctly -- this is always the
    # case, if the identifier results from the parsing of an equation but there
    # might be situations where the identifier is specified directly
    parse_result = list(IDENTIFIER.scanString(identifier))
    
    # parse_result[0][0][0] refers to the matched string -- this should be the
    # full identifier, if not it is an illegal identifier like "3foo" which only
    # matched on "foo" 
    if len(parse_result) != 1 or parse_result[0][0][0] != identifier:
        raise ValueError('"%s" is not a valid variable name.' % identifier)

    if keyword.iskeyword(identifier):
        raise ValueError(('"%s" is a Python keyword and cannot be used as a '
                          'variable.') % identifier)
    
    if identifier.startswith('_'):
        raise ValueError(('Variable "%s" starts with an underscore, '
                          'this is only allowed for variables used '
                          'internally') % identifier)

def check_identifier_reserved(identifier):
    '''
    Check that identifiers do not use the
    '''
    if identifier in ('t', 'dt', 'xi'):
        raise ValueError(('"%s" has a special meaning in equations and cannot '
                         ' be used as a variable name.') % identifier)


def check_identifier(identifier):
    '''
    Performs all the registered checks (via
    :meth:`Equations.register_identifier_check`) against ``identifier``, each
    raising a ValueError for illegal identfiers.
    '''
    for check_func in Equations.identifier_checks:
        check_func(identifier)


def parse_string_equations(eqns, namespace, exhaustive, level):
    """
    Parses a string defining equations and returns a dictionary, mapping
    variable names to :class:`Equations._Equation` objects.
    
    Arguments:
    ``namespace``
        An explictly given namespace (dictionary mapping names to objects)
    ``exhaustive``
        Whether the namespace in the namespace argument specifies the
        namespace completely (``True``) or should be used in addition to
        the locals/globals dictionaries (``False``)
    ``level``
        The level in the stack (an integer >=0) where to look for locals
        and globals
    
    """
    equations = {}
    
    try:
        parsed = EQUATIONS.parseString(eqns, parseAll=True)
    except ParseException as p_exc:
        raise ValueError('Parsing failed: \n' + str(p_exc.line) + '\n' +
                         ' '*(p_exc.column - 1) + '^\n' + str(p_exc))
    for eq in parsed:
        eq_type = eq.getName()
        eq_content = dict(eq.items())
        # Check for reserved keywords
        identifier = eq_content['identifier']
        
        # Convert unit string to Unit object
        unit = get_unit_from_string(eq_content['unit'])
        
        expression = eq_content.get('expression', None)
        if not expression is None:
            # Replace multiple whitespaces (arising from joining multiline
            # strings) with single space
            p = re.compile(r'\s{2,}')
            expression = p.sub(' ', expression)
        flags = list(eq_content.get('flags', []))

        equation = Equation(eq_type, identifier, expression, unit, flags,
                            namespace, exhaustive, level + 1) 
        
        if identifier in equations:
            raise ValueError('Duplicate definition of variable "%s"' %
                             identifier)
                                       
        equations[identifier] = equation
    
    return equations            

def resolve_equations(equations, variables):
    '''
    Resolve all the equations in the ``equations`` dictionary (see
    :meth:`CodeString.resolve`), treating the list of ``variables`` as internal
    variables.
    '''
    for eq in equations.itervalues():
        eq.resolve(variables)
    
    namespace = {}
    # Make absolutely sure there are no conflicts and nothing weird is
    # going on
    for eq in equations.itervalues():
        if eq.expr is None:
            # Parameters do not have/need a namespace
            continue
        for key, value in eq.expr._namespace.iteritems():
            if key in namespace:
                # Should refer to exactly the same object
                assert value is namespace[key] 
            else:
                namespace[key] = value
    
    return namespace

class Equation(object):
    '''
    Class for internal use, encapsulates a single equation or parameter.
    '''
    def __init__(self, eq_type, varname, expr, unit, flags,
                 namespace, exhaustive, level):
        '''
        Create a new :class:`_Equation` object.
        '''
        self.eq_type = eq_type
        self.varname = varname
        if eq_type != 'parameter':
            self.expr = CodeString(expr, namespace=namespace,
                                   exhaustive=exhaustive, level=level + 1)
        else:
            self.expr = None
        self.unit = unit
        self.flags = flags
        
        # will be set later in the sort_static_equations method of Equations
        self.update_order = -1

    # parameters do not depend on time
    is_time_dependent = property(lambda self: self.expr.is_time_dependent
                                 if not self.expr is None else False,
                                 doc='Whether this equation is time dependent')

    def resolve(self, internal_variables):
        '''
        Resolve all the variables (see :meth:`CodeString.resolve`),
        treating the list ``internal_variables`` as internal variables.
        '''
        if not self.expr is None:
            self.expr.resolve(internal_variables)        

    def __str__(self):
        if self.eq_type == 'diff_equation':
            s = 'd' + self.varname + '/dt'
        else:
            s = self.varname
        
        if not self.expr is None:
            s += ' = ' + str(self.expr)
        
        s += ' : ' + str(self.unit)
        
        if len(self.flags):
            s += '(' + ', '.join(self.flags) + ')'
        
        return s
    
    def __repr__(self):
        s = '<' + EQUATION_TYPE[self.eq_type] + ' ' + self.varname
        
        if not self.expr is None:
            s += ': ' + self.expr.code

        s += ' (Unit: ' + str(self.unit)
        
        if len(self.flags):
            s += ', flags: ' + ', '.join(self.flags)
        
        s += ')>'
        return s

class Equations(object):
    """Container that stores equations from which models can be created.
    
    Initialised as::
    
        Equations(eqs[, namespace=None][, exhaustive=False][, level=0])
    
    with arguments:
    
    ``eqs``
        A multiline string of equations (see below)
    ``namespace=None``
        An explictly given namespace (dictionary mapping names to objects)
    ``exhaustive=False``
        Whether the namespace in the namespace argument specifies the
        namespace completely (``True``) or should be used in addition to
        the locals/globals dictionaries (``False``)
    ``level=0``
        The level in the stack (an integer >=0) where to look for locals
        and globals 
           
    **String equations**
    
    String equations can be of any of the following forms:
    
    (1) ``dx/dt = f : unit (flags)`` (differential equation)
    (2) ``x = f : unit (flags)`` (equation)
    (3) ``x : unit (flags)`` (parameter)
    
    Equations can span several line and contain Python-style comments starting
    with ``#``
    
    """

    def __init__(self, eqns='', namespace=None, exhaustive=False, level=0):
        '''
        Constructs a new equations object from the multiline string ``eqns``,
        see :class:`Equations` for more details.
        '''
                
        self._equations = parse_string_equations(eqns, namespace, exhaustive,
                                                  level + 1)

        # Do a basic check for the identifiers
        self.check_identifiers()
        
        # Check for special symbol xi (stochastic term)
        uses_xi = None
        for eq in self._equations.itervalues():
            if not eq.expr is None and 'xi' in eq.expr.identifiers:
                if not eq.eq_type == 'diff_equation':
                    raise ValueError(('The equation defining %s contains the '
                                      'symbol "xi" but is not a differential '
                                      'equation.') % eq.varname)
                elif not uses_xi is None:
                    raise ValueError(('The equation defining %s contains the '
                                      'symbol "xi", but it is already used '
                                      'in the equation defining %s.') %
                                     (eq.varname, uses_xi))
                else:
                    uses_xi = eq.varname
        
        # Build the namespaces, resolve all external variables and rearrange
        # static equations
        self._namespace = resolve_equations(self._equations, self.variables)
        
        # Check the units for consistency
        self.check_units()

    def __iter__(self):
        return iter(self.equations.iteritems())

    # Class attribute: A set of functions that are used to check identifiers
    # Functions can be registered with the static method 
    # `:meth:Equations.register_identifier_check` and will be automatically
    # used when checking identifiers
    identifier_checks = set([check_identifier_basic,
                             check_identifier_reserved])
    
    @staticmethod
    def register_identifier_check(func):
        if not hasattr(func, '__call__'):
            raise ValueError('Can only register callables.')
        
        Equations.identifier_checks.add(func)

    def _is_linear(self, conditionally_linear=False):
        '''
        Whether all equations are linear and only refer to constant parameters.
        if ``conditionally_linear`` is ``True``, only checks for conditional
        linearity (i.e. all differential equations are linear with respect to
        themselves but not necessarily with respect to other differential
        equations).
        '''
        substitutions = {}        
        for eq in self.equations_ordered:
            # Skip parameters
            if eq.expr is None:
                continue
            
            expr = CodeString(word_substitute(eq.expr.code, substitutions),
                              self._namespace, exhaustive=True)
            
            if eq.eq_type == 'static_equation':
                substitutions.update({eq.varname: '(%s)' % expr.code})
            else:
                # This is a differential equation that we have to check
                                
                expr.resolve(self.names)
                
                identifiers = expr.identifiers
                
                # Check that it does not depend on time
                if 't' in identifiers:
                    return False
                
                # Check that it does not depend on non-constant parameters
                for parameter in self.parameter_names:
                    if (parameter in identifiers and
                        not 'constant' in self.equations[parameter].flags):
                        return False

                if conditionally_linear:
                    # Check for linearity against itself
                    if not expr.check_linearity(eq.varname):
                        return False
                else:
                    # Check against all state variables (not against static
                    # equation variables, these are already replaced)
                    for diff_eq_var in self.diff_eq_names:                    
                        if not expr.check_linearity(diff_eq_var):
                            return False

        # No non-linearity found
        return True

    def _get_units(self):
        '''
        Dictionary of all internal variables (including t, dt, xi) and their
        corresponding units
        '''
        units = dict([(var, eq.unit) for var, eq in
                      self._equations.iteritems()])
        units.update(UNITS_SPECIAL_VARS)
        return units

    # Properties
    
    equations = property(lambda self: self._equations,
                        doc='A dictionary mapping variable names to equations')
    equations_ordered = property(lambda self: sorted(self._equations.itervalues(),
                                                     key=lambda key: key.update_order),
                                 doc='A list of all equations, sorted '
                                 'according to the order in which they should '
                                 'be updated')
    
    diff_eq_expressions = property(lambda self: [(varname, eq.expr.frozen()) for 
                                                 varname, eq in self.equations.iteritems()
                                                 if eq.eq_type == 'diff_equation'],
                                  doc='A list of (variable name, expression) '
                                  'tuples of all differential equations.')
    
    eq_expressions = property(lambda self: [(varname, eq.expr.frozen()) for 
                                            varname, eq in self.equations.iteritems()
                                            if eq.eq_type in ('static_equation',
                                                              'diff_equation')],
                                  doc='A list of (variable name, expression) '
                                  'tuples of all equations.') 
    
    names = property(lambda self: [eq.varname for eq in self.equations_ordered])
    
    diff_eq_names = property(lambda self: [eq.varname for eq in self.equations_ordered
                                           if eq.eq_type == 'diff_equation'])
    static_eq_names = property(lambda self: [eq.varname for eq in self.equations_ordered
                                           if eq.eq_type == 'static_equation'])
    eq_names = property(lambda self: [eq.varname for eq in self.equations_ordered
                                           if eq.eq_type in ('diff_equation', 'static_equation')])
    parameter_names = property(lambda self: [eq.varname for eq in self.equations_ordered
                                             if eq.eq_type == 'parameter'])    
    
    is_linear = property(_is_linear)
    
    is_conditionally_linear = property(lambda self: self._is_linear(conditionally_linear=True),
                                       doc='Whether all equations are conditionally linear')
    
    units = property(_get_units)
    
    variables = property(lambda self: set(self.units.keys()),
                         doc='Set of all variables')
    
    def _sort_static_equations(self):
        '''
        Sorts the static equations in a way that resolves their dependencies
        upon each other. After this method has been run, the static equations
        returned by the ``equations_ordered`` property are in the order in which
        they should be updated
        '''
        
        # Get a dictionary of all the dependencies on other static equations,
        # i.e. ignore dependencies on parameters and differential equations
        static_deps = {}
        for eq in self._equations.itervalues():
            if eq.eq_type == 'static_equation':
                static_deps[eq.varname] = [dep for dep in eq.identifiers if
                                           dep in self._equations and
                                           self._equations[dep].eq_type == 'static_equation']
        
        # Use the standard algorithm for topological sorting:
        # http://en.wikipedia.org/wiki/Topological_sorting
                
        # List that will contain the sorted elements
        sorted_eqs = [] 
        # set of all nodes with no incoming edges:
        no_incoming = set([var for var, deps in static_deps.iteritems()
                           if len(deps) == 0]) 
        
        while len(no_incoming):
            n = no_incoming.pop()
            sorted_eqs.append(n)
            # find variables m depending on n
            dependent = [m for m, deps in static_deps.iteritems()
                         if n in deps]
            for m in dependent:
                static_deps[m].remove(n)
                if len(static_deps[m]) == 0:
                    # no other dependencies
                    no_incoming.add(m)
        if any([len(deps) > 0 for deps in static_deps.itervalues()]):
            raise ValueError('Cannot resolve dependencies between static '
                             'equations, dependencies contain a cycle.')
        
        # put the equations objects in the correct order
        for order, static_variable in enumerate(sorted_eqs):
            self._equations[static_variable].update_order = order
        
        # Sort differential equations and parameters after static equations
        for eq in self._equations.itervalues():
            if eq.eq_type == 'diff_equation':
                eq.update_order = len(sorted_eqs)
            elif eq.eq_type == 'parameter':
                eq.update_order = len(sorted_eqs) + 1

    def check_units(self):
        '''
        Check all the units for consistency and raise a 
        :class:`DimensionMismatchError` in case of errors.
        '''
        units = self.units
        for var, eq in self._equations.iteritems():
            if eq.eq_type == 'parameter':
                # no need to check units for parameters
                continue
            
            if eq.eq_type == 'diff_equation':
                try:
                    eq.expr.check_unit_against(units[var] / second, units)
                except DimensionMismatchError as dme:
                    raise DimensionMismatchError(('Differential equation defining '
                                                  '%s does not use consistent units: %s') % 
                                                 (var, dme.desc), *dme.dims)
            elif eq.eq_type == 'static_equation':
                try:
                    eq.expr.check_unit_against(units[var], units)
                except DimensionMismatchError as dme:
                    raise DimensionMismatchError(('Static equation defining '
                                                  '%s does not use consistent units: %s') % 
                                                 (var, dme.desc), *dme.dims)                
            else:
                raise AssertionError('Unknown equation type: "%s"' % eq.eq_type)

    def check_identifiers(self):
        '''
        Checks the list of identifiers used in this equation against the given
        list of reserved identifiers (also performs some standard checks like
        not allowing Python keywords, see :func:`check_identifier_basic`).
        '''
        for name in self.names:            
            check_identifier(name)

    def check_flags(self, allowed_flags):
        '''
        Checks the list of flags against the flags contained in
        ``allowed_flags``, which should be a dictionary mapping equation types
        (``parameter``, ``diff_equation``, ``static_equation``) to a list
        of strings (the allowed flags for that equation type). Not specifying
        allowed flags for an equation type is the same as specifying an empty
        list for it.
        '''
        for eq in self.equations.itervalues():
            for flag in eq.flags:
                if not eq.eq_type in allowed_flags or len(allowed_flags[eq.eq_type]) == 0:
                    raise ValueError('Equations of type "%s" cannot have any flags.' % EQUATION_TYPE[eq.eq_type])
                if not flag in allowed_flags[eq.eq_type]:
                    raise ValueError(('Equations of type "%s" cannot have a '
                                      'flag "%s", only the following flags '
                                      'are allowed: %s') % (EQUATION_TYPE[eq.eq_type],
                                                            flag, allowed_flags[eq.eq_type]))

    #
    # Representation
    # 

    def __str__(self):
        strings = [str(eq) for eq in self._equations.itervalues()]
        return '\n'.join(strings)

    def _repr_pretty_(self, p, cycle):
        ''' Pretty printing for ipython '''
        if cycle: 
            # Should never happen actually
            return 'Equations(...)'
        for eq in self._equations.itervalues():
            p.pretty(eq)
