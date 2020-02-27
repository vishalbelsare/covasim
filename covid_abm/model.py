'''
This file contains all the code for a single run of Covid-ABM.

Based heavily on LEMOD-FP (https://github.com/amath-idm/lemod_fp).
'''

#%% Imports
import numpy as np # Needed for a few things not provided by pl
import pylab as pl
import sciris as sc
from . import utils as cov_ut
from . import parameters as cov_pars
from . import poisson_stats as cov_ps

# Specify all externally visible functions this file defines
__all__ = ['ParsObj', 'Person', 'Sim', 'single_run', 'multi_run']



#%% Define classes
class ParsObj(sc.prettyobj):
    '''
    A class based around performing operations on a self.pars dict.
    '''

    def __init__(self, pars):
        self.update_pars(pars)
        self.results_keys = ['t',
                             'n_susceptible', 
                             'n_exposed', 
                             'n_infectious', 
                             'n_recovered',
                             'infections', 
                             'tests', 
                             'diagnoses', 
                             'recoveries',
                             'cum_exposed', 
                             'cum_tested', 
                             'cum_diagnosed']
        return

    def __getitem__(self, key):
        ''' Allow sim['par_name'] instead of sim.pars['par_name'] '''
        return self.pars[key]

    def __setitem__(self, key, value):
        ''' Ditto '''
        self.pars[key] = value
        return

    def update_pars(self, pars):
        ''' Update internal dict with new pars '''
        if not hasattr(self, 'pars'):
            self.pars = pars
        elif pars is not None:
            self.pars.update(pars)
        return
    

class Person(ParsObj):
    '''
    Class for a single person.
    '''
    def __init__(self, pars, age=0, sex=0, crew=False):
        super().__init__(pars) # Set parameters
        self.uid = str(pl.randint(0,1e9)) # Unique identifier for this person
        self.age = float(age) # Age of the person (in years)
        self.sex = sex # Female (0) or male (1)
        self.crew       = crew # Wehther the person is a crew member
        if self.crew:
            self.contacts = self.pars['contacts_crew'] # Determine how many contacts they have
        else:
            self.contacts = self.pars['contacts_guest']
        
        # Define state
        self.on_ship    = True # Whether the person is still on the ship
        self.alive      = True
        self.exposed    = False
        self.infectious = False
        self.diagnosed  = False
        self.recovered  = False
        
        # Keep track of dates
        self.date_exposed    = None
        self.date_infectious = None
        self.date_diagnosed  = None
        self.date_recovered  = None
        return


class Sim(ParsObj):
    '''
    The Sim class handles the running of the simulation: the number of children,
    number of time points, and the parameters of the simulation.
    '''

    def __init__(self, pars=None, datafile=None):
        if pars is None:
            print('Note: using default parameter values')
            pars = cov_pars.make_pars()
        super().__init__(pars) # Initialize and set the parameters as attributes
        self.data = cov_pars.load_data(datafile)
        cov_ut.set_seed(self.pars['seed'])
        self.init_results()
        self.init_people()
        self.interventions = {}
        return
    
    @property
    def n(self):
        return len(self.people)
    
    @property
    def npts(self):
        return int(self.pars['n_days'] + 1)

    @property
    def tvec(self):
        return np.arange(self.pars['n_days'] + 1)


    def init_results(self):
        self.results = {}
        for key in self.results_keys:
            self.results[key] = np.zeros(int(self.npts))
        self.results['ready'] = False
        return
    

    def init_people(self, seed_infections=1):
        ''' Create the people '''
        self.people = sc.odict() # Dictionary for storing the people
        self.off_ship = sc.odict() # For people who've been moved off the ship
        guests = [0]*self.pars['n_guests']
        crew   = [1]*self.pars['n_crew']
        for is_crew in crew+guests: # Loop over each person
            age,sex = cov_pars.get_age_sex(is_crew)
            person = Person(self.pars, age=age, sex=sex, crew=is_crew) # Create the person
            self.people[person.uid] = person # Save them to the dictionary
        
        # Create the seed infections
        for i in range(seed_infections):
            self.people[i].exposed = True
            self.people[i].infectious = True
            self.people[i].date_exposed = 0
            self.people[i].date_infectious = 0
        
        return

    
    def day2ind(self, day):
        index = int(day)
        return index
    
    
    def ind2day(self, ind):
        day = ind
        return day
    
    
    def summary_stats(self):
        keys = ['n_susceptible', 'n_exposed', 'n_infectious']
        summary = {}
        for key in keys:
            summary[key] = self.results[key][-1]
        return summary
    
    
    def run(self, seed_infections=1, verbose=None, calc_likelihood=False, do_plot=False, **kwargs):
        ''' Run the simulation '''
        
        T = sc.tic()
        
        # Reset settings and results
        if verbose is None:
            verbose = self.pars['verbose']
        self.init_results()
        self.init_people(seed_infections=seed_infections) # Actually create the people
        daily_tests = self.data['new_tests'] # Number of tests each day, from the data
        
        # Main simulation loop
        for t in range(self.npts):
            
            test_probs = {} # Store the probability of each person getting tested
            
            # Print progress
            if verbose>-1:
                string = f'  Running day {t:0.0f} of {self.pars["n_days"]}...'
                if verbose>0:
                    sc.heading(string)
                else:
                    print(string)
            
            # Update each person
            for person in self.people.values():
                
                # Handle testing probability
                if person.infectious:
                    test_probs[person.uid] = self.pars['symptomatic'] # They're infectious: high probability of testing
                else:
                    test_probs[person.uid] = 1.0
                
                # If exposed, check if the person becomes infectious
                if person.exposed:
                    self.results['n_exposed'][t] += 1
                    if not person.infectious and t >= person.date_infectious: # It's the day they become infectious
                        person.infectious = True
                        if verbose>0:
                            print(f'      Person {person.uid} became infectious!')
                        
                # If infectious, check if anyone gets infected
                if person.infectious:
                    # First, check for recovery
                    if t >= person.date_recovered: # It's the day they become infectious
                        person.exposed = False
                        person.infectious = False
                        person.recovered = True
                        self.results['recoveries'][t] += 1
                    else:
                        self.results['n_infectious'][t] += 1 # Count this person as infectious
                        n = np.random.poisson(person.contacts, 1) # Draw the number of Poisson contacts for this person
                        contact_inds = cov_ut.choose_people(max_ind=len(self.people), n=n) # Choose people at random
                        for contact_ind in contact_inds:
                            exposure = cov_ut.bt(self.pars['r_contact']) # Check for exposure per person
                            if exposure:
                                target_person = self.people[contact_ind]
                                if not target_person.exposed: # Skip people already exposed
                                    self.results['infections'][t] += 1
                                    target_person.exposed = True
                                    target_person.date_exposed = t
                                    incub_dist = round(pl.normal(person.pars['incub'], person.pars['incub_std']))
                                    dur_dist = round(pl.normal(person.pars['dur'], person.pars['dur_std']))
                                    target_person.date_infectious = t + incub_dist
                                    target_person.date_recovered = target_person.date_infectious + dur_dist
                                    if verbose>0:
                                        print(f'        Person {person.uid} infected person {target_person.uid}!')
                
                # Count people who recovered
                if person.recovered:
                    self.results['n_recovered'][t] += 1
            
            # Implement testing -- this is outside of the loop over people, but inside the loop over time
            if t<len(daily_tests): # Don't know how long the data is, ensure we don't go past the end
                n_tests = daily_tests.iloc[t] # Number of tests for this day
                if n_tests and not pl.isnan(n_tests): # There are tests this day
                    self.results['tests'][t] = n_tests # Store the number of tests
                    test_probs = pl.array(test_probs.value())
                    test_probs /= test_probs.sum()
                    test_inds = cov_ut.choose_people_weighted(probs=test_probs, n=n_tests)
                    uids_to_pop = []
                    for test_ind in test_inds:
                        tested_person = self.people[test_ind]
                        if tested_person.infectious and cov_ut.bt(self.pars['sensitivity']): # Person was tested and is true-positive
                            self.results['diagnoses'][t] += 1
                            tested_person.diagnosed = True
                            uids_to_pop.append(tested_person.uid)
                            if verbose>0:
                                        print(f'          Person {person.uid} was diagnosed!')
                    for uid in uids_to_pop: # Remove people from the ship once they're diagnosed
                        self.off_ship[uid] = self.people.pop(uid)
                            
            # Implement quarantine
            if t == self.pars['quarantine']:
                print('Implementing quarantine...')
                for person in self.people.values():
                    person.contacts *= self.pars['quarantine_eff'] # TODO: separate factors for crew and guests
            
            # Implement testing chnage
            if t == self.pars['testing_change']:
                print('Implementing testing change...')
                self.pars['symptomatic'] *= self.pars['testing_symptoms'] # Reduce the proportion of symptomatic testing
            
            # Implement evacuations
            print('Not implemented') # TODO -- American and final evacuations
                        
            # Store other results
            self.results['t'][t] = t
            self.results['n_susceptible'][t] = len(self.people) - self.results['n_exposed'][t]
        
        # Compute cumulative results
        self.results['cum_exposed']   = pl.cumsum(self.results['infections'])
        self.results['cum_tested']    = pl.cumsum(self.results['tests'])
        self.results['cum_diagnosed'] = pl.cumsum(self.results['diagnoses'])
        
        # Comute likelihood
        if calc_likelihood:
            self.likelihood()
        
        # Tidy up
        self.results['ready'] = True
        elapsed = sc.toc(T, output=True)
        print(f'\nRun finished after {elapsed:0.1f} s.\n')
        summary = self.summary_stats()
        print(f"""Summary: 
     {summary['n_susceptible']:5.0f} susceptible 
     {summary['n_exposed']:5.0f} exposed
     {summary['n_infectious']:5.0f} infectious
           """)
         
        if do_plot:
            self.plot(**kwargs)
        
        return self.results
    
    
    def likelihood(self, verbose=None):
        '''
        Compute the log-likelihood of the current simulation based on the number
        of new diagnoses.
        '''
        if verbose is None:
            verbose = self.pars['verbose']
        if verbose:
            print('Calculating likelihood...')
        
        if not self.results['ready']:
            self.run(calc_likelihood=False, verbose=verbose) # To avoid an infinite loop
        
        loglike = 0
        for d,datum in enumerate(self.data['new_positives']):
            if not pl.isnan(datum): # Skip days when no tests were performed
                estimate = self.results['diagnoses'][d]
                p = cov_ps.poisson_test(datum, estimate)
                logp = pl.log(p)
                loglike += logp
                if verbose>1:
                    print(f'  {self.data["date"][d]}, data={datum:3.0f}, model={estimate:3.0f}, log(p)={logp:10.4f}, loglike={loglike:10.4f}')
        
        self.results['likelihood'] = loglike
        
        return loglike
        

    
    def plot(self, do_save=None, fig_args=None, plot_args=None, scatter_args=None, axis_args=None, as_days=True, font_size=16, verbose=None):
        '''
        Plot the results -- can supply arguments for both the figure and the plots.

        Parameters
        ----------
        do_save : bool or str
            Whether or not to save the figure. If a string, save to that filename.

        fig_args : dict
            Dictionary of kwargs to be passed to pl.figure()

        plot_args : dict
            Dictionary of kwargs to be passed to pl.plot()
        
        as_days : bool
            Whether to plot the x-axis as days or time points

        Returns
        -------
        Figure handle
        '''
        
        if verbose is None:
            verbose = self.pars['verbose']
        if verbose:
            print('Plotting...')

        if fig_args     is None: fig_args     = {'figsize':(26,16)}
        if plot_args    is None: plot_args    = {'lw':3, 'alpha':0.7, 'marker':'o'}
        if scatter_args is None: scatter_args = {'s':150, 'marker':'s'}
        if axis_args    is None: axis_args    = {'left':0.1, 'bottom':0.05, 'right':0.9, 'top':0.97, 'wspace':0.2, 'hspace':0.25}

        fig = pl.figure(**fig_args)
        pl.subplots_adjust(**axis_args)
        pl.rcParams['font.size'] = font_size

        res = self.results # Shorten since heavily used

        # Plot everything
        colors = sc.gridcolors(5)
        to_plot = sc.odict({ # TODO
            'Total counts': sc.odict({'n_susceptible':'Number susceptible', 
                                    'n_exposed':'Number exposed', 
                                    'n_infectious':'Number infectious',
                                    'cum_diagnosed':'Number diagnosed',
                                    }),
            'Daily counts': sc.odict({'infections':'New infections',
                                 'tests':'Number of tests',
                                 'diagnoses':'New diagnoses', 
                                 }),
            })
        for p,title,keylabels in to_plot.enumitems():
            pl.subplot(2,1,p+1)
            for i,key,label in keylabels.enumitems():
                this_color = colors[i+p] # TODO: Fix Matplotlib complaints
                y = res[key]
                pl.plot(res['t'], y, label=label, **plot_args, c=this_color)
                if key == 'diagnoses': # TODO: fix up labeling issue
                    pl.scatter(self.data['day'], self.data['new_positives'], c=[this_color], **scatter_args)
                elif key == 'tests': # TODO: fix up labeling issue
                    pl.scatter(self.data['day'], self.data['new_tests'], c=[this_color], **scatter_args)
                    pl.scatter(pl.nan, pl.nan, c=[(0,0,0)], label='Data', **scatter_args)
            cov_ut.fixaxis()
            pl.ylabel('Count')
            pl.xlabel('Day')
            pl.title(title, fontweight='bold')

        # Ensure the figure actually renders or saves
        if do_save:
            if isinstance(do_save, str):
                filename = do_save # It's a string, assume it's a filename
            else:
                filename = 'covid_abm_results.png' # Just give it a default name
            pl.savefig(filename)
        
        pl.show()

        return fig
    
    
    def plot_people(self):
        ''' Use imshow() to show all individuals as rows, with time as columns, one pixel per timestep per person '''
        raise NotImplementedError


def single_run(sim):
    sim.run()
    return sim


def multi_run(orig_sim, n=4, verbose=None):
    
    # Copy the simulations
    sims = []
    for i in range(n):
        new_sim = sc.dcp(orig_sim)
        new_sim.pars['seed'] += i # Reset the seed, otherwise no point!
        new_sim.pars['n'] = int(new_sim.pars['n']/n) # Reduce the population size accordingly
        sims.append(new_sim)
        
    finished_sims = sc.parallelize(single_run, iterarg=sims)
    
    output_sim = sc.dcp(finished_sims[0])
    output_sim.pars['parallelized'] = n # Store how this was parallelized
    output_sim.pars['n'] *= n # Restore this since used in later calculations -- a bit hacky, it's true
    
    for sim in finished_sims[1:]: # Skip the first one
        output_sim.people.update(sim.people)
        for key,val in sim.results.items():
            if key != 't':
                output_sim.results[key] += sim.results[key]
    
    return output_sim
    
    
    