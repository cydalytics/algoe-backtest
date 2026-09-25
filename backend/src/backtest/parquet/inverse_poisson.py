import numpy as np
from scipy.optimize import minimize
from .poisson_betting import calculate_match_probabilities, calculate_asian_handicap_odds, calculate_over_under_odds##, calculate_handicap_had_odds, calculate_correct_score_odds, calculate_total_goals_odds, calculate_odd_even_odds, calculate_ht_ft_odds

def odds_to_probs(odds_dict):
    """
    Converts decimal odds to normalized probabilities.
    Removes the bookmaker's margin (overround) proportionally.
    
    Args:
        odds_dict (dict): e.g., {'Home': 2.0, 'Draw': 3.2, ...}
        
    Returns:
        dict: Normalized probabilities e.g., {'Home': 0.48, 'Draw': 0.30, ...}
    """
    # Calculate implied probabilities (1 / odds)
    implied_probs = {k: 1/v for k, v in odds_dict.items() if v > 0}
    
    # Sum of implied probabilities (usually > 1 due to margin)
    total_implied = sum(implied_probs.values())
    
    # Normalize to sum to 1.0
    return {k: v/total_implied for k, v in implied_probs.items()}

def objective_function(x, market_data, optimize_draw=False, optimize_fh=False):
    """
    Error function to minimize. Calculates the difference between
    market probabilities and model probabilities for given expectancies.
    """
    total_goals = x[0]
    supremacy = x[1]
    
    idx = 2
    if optimize_draw:
        draw_factor = x[idx]
        idx += 1
    else:
        draw_factor = 1.067
        
    if optimize_fh:
        fh_factor = x[idx]
        idx += 1
    else:
        fh_factor = 0.54
    
    # Penalty for non-positive expectancies
    if total_goals <= 0.01:
        return 1e9
        
    total_error = 0
    
    # 1. Match Odds (1X2)
    if '1x2' in market_data:
        target_probs = odds_to_probs(market_data['1x2'])
        model_probs = calculate_match_probabilities(total_goals, supremacy, draw_factor=draw_factor)
        
        total_error += (model_probs['Home Win'] - target_probs['Home'])**2
        total_error += (model_probs['Draw'] - target_probs['Draw'])**2
        total_error += (model_probs['Away Win'] - target_probs['Away'])**2

    # 2. Asian Handicap
    if 'handicap' in market_data:
        h_data = market_data['handicap']
        line = h_data['line']
        target_probs = odds_to_probs(h_data['odds']) # {'Home': 1.9, 'Away': 1.9}
        
        # Calculate model odds
        model_result = calculate_asian_handicap_odds(total_goals, supremacy, line, draw_factor=draw_factor)
        
        # Reconstruct keys used in poisson_betting.py
        home_key = f"Home"
        away_key = f"Away"
        
        # Convert model Fair Odds back to probabilities (1 / Odds)
        # Handle potential 0 odds (though unlikely with valid inputs)
        p_home = 1 / model_result[home_key] if model_result.get(home_key, 0) > 0 else 0
        p_away = 1 / model_result[away_key] if model_result.get(away_key, 0) > 0 else 0
        
        total_error += (p_home - target_probs['Home'])**2
        total_error += (p_away - target_probs['Away'])**2

    # 3. Over/Under
    if 'ou' in market_data:
        ou_data = market_data['ou']
        line = ou_data['line']
        target_probs = odds_to_probs(ou_data['odds']) # {'Over': 1.9, 'Under': 1.9}
        
        model_result = calculate_over_under_odds(total_goals, supremacy, line, draw_factor=draw_factor)
        
        over_key = f"Over"
        under_key = f"Under"
        
        p_over = 1 / model_result[over_key] if model_result.get(over_key, 0) > 0 else 0
        p_under = 1 / model_result[under_key] if model_result.get(under_key, 0) > 0 else 0
        
        total_error += (p_over - target_probs['Over'])**2
        total_error += (p_under - target_probs['Under'])**2
        


    return total_error

def solve_expectancy(market_data, optimize_draw=False, optimize_fh=False):
    """
    Finds the Total Goals and Supremacy that best fit the market odds.
    """
    # Create a copy of market_data with demarginalized odds
    clean_market_data = {}
    
    if '1x2' in market_data:
        odds_dict = market_data['1x2']
        keys = list(odds_dict.keys())
        values = [odds_dict[k] for k in keys]
        clean_values = demarginalize_sell_odds(values)
        clean_market_data['1x2'] = dict(zip(keys, clean_values))
        
    if 'handicap' in market_data:
        clean_market_data['handicap'] = {'line': market_data['handicap']['line']}
        odds_dict = market_data['handicap']['odds']
        keys = list(odds_dict.keys())
        values = [odds_dict[k] for k in keys]
        clean_values = demarginalize_sell_odds(values)
        clean_market_data['handicap']['odds'] = dict(zip(keys, clean_values))
        
    if 'ou' in market_data:
        clean_market_data['ou'] = {'line': market_data['ou']['line']}
        odds_dict = market_data['ou']['odds']
        keys = list(odds_dict.keys())
        values = [odds_dict[k] for k in keys]
        clean_values = demarginalize_sell_odds(values)
        clean_market_data['ou']['odds'] = dict(zip(keys, clean_values))



    # Initial guess: 3.0 goals total, 0.0 supremacy
    initial_guess = [3.0, 0.0]
    bounds = [(0.01, 10.0), (-5.0, 5.0)]
    
    if optimize_draw:
        initial_guess.append(1.0)
        bounds.append((0.5, 1.5))
        
    if optimize_fh:
        initial_guess.append(0.45)
        bounds.append((0.2, 0.7))
    
    result = minimize(
        objective_function, 
        initial_guess, 
        args=(clean_market_data, optimize_draw, optimize_fh), 
        bounds=bounds,
        # method='Nelder-Mead'
        method='L-BFGS-B'
    )
    
    return result.x

def demarginalize_sell_odds(odds_list):
    """
    Given a list of sell odds, remove the bookmaker's margin
    and return demarginalized odds.
    
    Args:
        odds_list (list): List of decimal odds (floats)
    Returns:
        list: Demarginalized odds
        
    """
    # Calculate implied probabilities
    implied_probs = [1.0 / odd for odd in odds_list if odd > 0]
    
    # Sum of implied probabilities
    total_implied = sum(implied_probs)
    
    # Normalize probabilities
    normalized_probs = [p / total_implied for p in implied_probs]
    
    # Convert back to odds
    demarginalized_odds = [1.0 / p if p > 0 else 0 for p in normalized_probs]
    
    return demarginalized_odds

if __name__ == "__main__":
    # --- Configuration ---
    # Enter the market odds here
    market_data = {
        # 1X2 Odds (Home, Draw, Away)
        # '1x2': {'Home': 2.1, 'Draw': 2.11, 'Away': 5.20},
        
        # Asian Handicap (Line, Home Odds, Away Odds)
        'handicap': {
            'line': -0.5, 
            'odds': {'Home': 2.06, 'Away': 1.82}
        },
        
        # Over/Under (Line, Over Odds, Under Odds)
        'ou': {
            'line': 1.25, 
            'odds': {'Over': 1.88, 'Under': 2.0}
        },
        
                
        # Handicap HAD (Line, Home, Draw, Away Odds)
        # 'hhad': {
        #     'line': -1.0,
        #     'odds': {'Home': 2.40, 'Draw': 3.25, 'Away': 2.40}
        # },
        
        # # Correct Score (Score: Odds)
        # 'crs': {
        #     '1-0': 5.8,
        #     '2-0': 6.0,
        #     '2-1': 6.9,
        #     '0-0': 9.5,
        #     '1-1': 7.25,
        #     '0-1': 14.5,
        #     '0-2': 30.0,
        #     '1-2': 17.0,
        # },
        
        # # Total Goals
        # 'tgl': {
        #     'count_plus_threshold': 7,
        #     'odds': {
        #         '0': 9.5,
        #         '1': 4.35,
        #         '2': 3.35,
        #         '3': 3.75,
        #         '4': 5.2,
        #         '5': 8.75,
        #         '6': 18.0,
        #         '7+': 25.0
        #     }
        # },
        
        # # Odd/Even (Odd, Even Odds)
        # 'oe': {'Odd': 1.88, 'Even': 1.82},
        
        # # First Half 1X2 (HAD)
        # 'fh_1x2': {'Home': 1.84, 'Draw': 2.30, 'Away': 6.10},
        
        # # First Half Asian Handicap
        # 'fh_handicap': {
        #     'line': -0.25, 
        #     'odds': {'Home': 1.63, 'Away': 2.13}
        # },
        
        # # First Half Over/Under (HILO)
        # 'fh_ou': {
        #     'line': 1.5, 
        #     'odds': {'Over': 2.65, 'Under': 1.41}
        # },
        
        # # First Half Correct Score (CRS)
        # 'fh_crs': {
        #     '1-0': 3.3,
        #     '2-0': 6.1,
        #     '2-1': 13.5,
        #     '0-0': 2.68,
        #     '1-1': 7.25,
        #     '0-1': 7.0,
        #     '0-2': 30.0,
        #     '1-2': 35.0,
        # },
        
        # # Half-Time / Full-Time
        # 'ht_ft': {
        #     'Home/Home': 2.10,
        #     'Home/Draw': 16.0,
        #     'Home/Away': 45.0,
        #     'Draw/Home': 3.75,
        #     'Draw/Draw': 5.5,
        #     'Draw/Away': 12.50,
        #     'Away/Home': 23.0,
        #     'Away/Draw': 16.0,
        #     'Away/Away': 10.0
        # }
    }
    
    print("Solving for Expected Goals based on Market Data...")
    print("-" * 40)
    if '1x2' in market_data:
        print(f"Input 1X2:      {market_data['1x2']}")
    if 'handicap' in market_data:
        print(f"Input Handicap: {market_data['handicap']['line']} -> {market_data['handicap']['odds']}")
    if 'ou' in market_data:
        print(f"Input O/U:      {market_data['ou']['line']} -> {market_data['ou']['odds']}")
    if 'hhad' in market_data:
        print(f"Input HHAD:     {market_data['hhad']['line']} -> {market_data['hhad']['odds']}")
    if 'crs' in market_data:
        print(f"Input CRS:      {len(market_data['crs'])} scores -> {market_data['crs']}")
    if 'tgl' in market_data:
        print(f"Input TGL:      Threshold {market_data['tgl'].get('count_plus_threshold', 'None')} -> {market_data['tgl']['odds']}")
    if 'oe' in market_data:
        print(f"Input O/E:      {market_data['oe']}")
    if 'fh_1x2' in market_data:
        print(f"Input FH 1X2:   {market_data['fh_1x2']}")
    if 'ht_ft' in market_data:
        print(f"Input HT/FT:    {market_data['ht_ft']}")
    print("-" * 40)

    # Note: Enable optimize_fh=True so the solver finds the best First Half ratio
    result_x = solve_expectancy(market_data, optimize_draw=False, optimize_fh=False)
    total_goals = result_x[0]
    supremacy = result_x[1]
    
    idx = 2
    draw_factor = result_x[idx] if len(result_x) > idx else 1.067; idx += 1
    fh_factor = result_x[idx] if len(result_x) > idx else 0.54
    
    print(f"\nEstimated Parameters:")
    print(f"Total Goals: {total_goals:.4f}")
    print(f"Supremacy: {supremacy:.4f}")
    print(f"Draw Factor: {draw_factor:.4f}")
    print(f"FH Factor:   {fh_factor:.4f} (FH Goals: {total_goals * fh_factor:.4f}, FH Sup: {supremacy * fh_factor:.4f})")
    print(f"Total Goals: {total_goals:.4f}")
    print(f"Supremacy: {supremacy:.4f}")
    print(f"Draw Factor: {draw_factor:.4f}")
    
    # --- Verification ---
    print("\n" + "="*40)
    print("Verification: Model Output vs Market (Normalized)")
    print("="*40)
    
    # 1X2
    if '1x2' in market_data:
        target_1x2 = odds_to_probs(market_data['1x2'])
        model_1x2 = calculate_match_probabilities(total_goals, supremacy, draw_factor=draw_factor)
        print(f"1X2 Home: Model {model_1x2['Home Win']:.2%} vs Market {target_1x2['Home']:.2%}")
        print(f"1X2 Draw: Model {model_1x2['Draw']:.2%} vs Market {target_1x2['Draw']:.2%}")
        print(f"1X2 Away: Model {model_1x2['Away Win']:.2%} vs Market {target_1x2['Away']:.2%}")
        
    # Handicap
    if 'handicap' in market_data:
        h_line = market_data['handicap']['line']
        target_h = odds_to_probs(market_data['handicap']['odds'])
        model_h_odds = calculate_asian_handicap_odds(total_goals, supremacy, h_line, draw_factor=draw_factor)
        model_h_prob = 1 / model_h_odds[f"Home"]
        print(f"Handicap Home ({h_line}): Model {model_h_prob:.2%} vs Market {target_h['Home']:.2%}")
        
    # O/U
    if 'ou' in market_data:
        ou_line = market_data['ou']['line']
        target_ou = odds_to_probs(market_data['ou']['odds'])
        model_ou_odds = calculate_over_under_odds(total_goals, supremacy, ou_line, draw_factor=draw_factor)
        model_ou_prob = 1 / model_ou_odds[f"Over"]
        print(f"Over {ou_line}: Model {model_ou_prob:.2%} vs Market {target_ou['Over']:.2%}")

