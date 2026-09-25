import numpy as np
from scipy.stats import poisson

def marginalize_fair_odds(fair_odds_list, margin_factor):
    """
    Given a list of fair decimal odds (whose implied probabilities sum to 1.0),
    apply a bookmaker's margin and return the selling odds.
    
    Args:
        fair_odds_list (list): List of fair decimal odds.
        margin_factor (float): Target overround factor (e.g., 1.05 for a 5% margin).
        
    Returns:
        list: Marginalized selling odds.
    """
    implied_probs = [1.0 / odd if odd > 0 else 0 for odd in fair_odds_list]
    marginalized_odds = [1.0 / (p * margin_factor) if p > 0 else 0 for p in implied_probs]
    return marginalized_odds

def calculate_match_probabilities(total_goals, supremacy, max_goals=10, draw_factor=1.0):
    """
    Calculates the probabilities of Home Win, Draw, and Away Win
    using the Poisson distribution.

    Args:
        total_goals (float): Expected total goals (home + away).
        supremacy (float): Expected goal difference (home - away).
        max_goals (int): Maximum number of goals to consider for calculation.
        draw_factor (float): Factor to adjust the probability of a draw. Default is 1.0.

    Returns:
        dict: Probabilities for 'Home Win', 'Draw', 'Away Win'.
    """
    home_avg_goals = (total_goals + supremacy) / 2.0
    away_avg_goals = (total_goals - supremacy) / 2.0
    
    # Ensure non-negative expectancies
    if home_avg_goals < 0: home_avg_goals = 0
    if away_avg_goals < 0: away_avg_goals = 0
    
    # Generate range of goals
    goals_range = np.arange(max_goals + 1)
    
    # Probability of scoring exactly k goals (vectorized)
    home_probs = poisson.pmf(goals_range, home_avg_goals)
    away_probs = poisson.pmf(goals_range, away_avg_goals)

    # Calculate joint probability matrix (outer product)
    # matrix[i, j] represents probability of Home scoring i and Away scoring j
    prob_matrix = np.outer(home_probs, away_probs)
    
    # Apply draw factor
    if draw_factor != 1.0:
        diag_indices = np.diag_indices_from(prob_matrix)
        prob_matrix[diag_indices] *= draw_factor
        
    prob_matrix /= prob_matrix.sum()  # Normalize to ensure total probability is 1

    # Sum probabilities based on indices
    # Home Win: i > j (lower triangle of the matrix, excluding diagonal)
    home_win_prob = np.sum(np.tril(prob_matrix, -1))
    
    # Draw: i == j (diagonal)
    draw_prob = np.sum(np.diag(prob_matrix))
    
    # Away Win: i < j (upper triangle, excluding diagonal)
    away_win_prob = np.sum(np.triu(prob_matrix, 1))

    return {
        "Home Win": home_win_prob,
        "Draw": draw_prob,
        "Away Win": away_win_prob
    }



def calculate_asian_handicap_odds(total_goals, supremacy, handicap, max_goals=10, draw_factor=1.0):
    """
    Calculates fair odds for Asian Handicap lines, including quarter lines (e.g., -1.75).
    There is no 'Draw' outcome in Asian Handicap (2-way market).
    
    Args:
        total_goals (float): Expected total goals (home + away).
        supremacy (float): Expected goal difference (home - away).
        handicap (float): Home team handicap (e.g., -1.75).
        draw_factor (float): Factor to adjust the probability of a draw. Default is 1.0.
        
    Returns:
        dict: Fair decimal odds for Home and Away.
    """
    home_avg_goals = (total_goals + supremacy) / 2.0
    away_avg_goals = (total_goals - supremacy) / 2.0
    
    # Ensure non-negative expectancies
    if home_avg_goals < 0: home_avg_goals = 0
    if away_avg_goals < 0: away_avg_goals = 0

    # Generate probability matrix
    goals_range = np.arange(max_goals + 1)
    home_probs = poisson.pmf(goals_range, home_avg_goals)
    away_probs = poisson.pmf(goals_range, away_avg_goals)
    prob_matrix = np.outer(home_probs, away_probs)
    
    # Apply draw factor
    if draw_factor != 1.0:
        diag_indices = np.diag_indices_from(prob_matrix)
        prob_matrix[diag_indices] *= draw_factor
        
    prob_matrix /= prob_matrix.sum()  # Normalize

    # Goal difference matrix (Home Goals - Away Goals)
    i, j = np.indices(prob_matrix.shape)
    goal_diff = i - j
    
    # Check if it's a quarter line (e.g., 0.25, 0.75, 1.25, 1.75)
    # A quarter line ends in .25 or .75. 
    # Multiplied by 4, it should be an odd integer.
    is_quarter = (abs(handicap) * 4) % 2 != 0
    
    if is_quarter:
        # Split into two lines
        line1 = handicap + 0.25
        line2 = handicap - 0.25
        
        # Calculate outcomes for Home team
        # We evaluate (Goal Diff + Line)
        
        # Line 1 outcomes
        l1_win = (goal_diff + line1) > 0
        l1_push = np.isclose(goal_diff + line1, 0)
        l1_loss = (goal_diff + line1) < 0
        
        # Line 2 outcomes
        l2_win = (goal_diff + line2) > 0
        l2_push = np.isclose(goal_diff + line2, 0)
        l2_loss = (goal_diff + line2) < 0
        
        # Combine outcomes to find probabilities of Full Win, Half Win, etc.
        # Full Win: Win on both
        prob_full_win = np.sum(prob_matrix[l1_win & l2_win])
        
        # Half Win: Win on one, Push on other
        prob_half_win = np.sum(prob_matrix[(l1_win & l2_push) | (l1_push & l2_win)])
        
        # Half Loss: Loss on one, Push on other
        prob_half_loss = np.sum(prob_matrix[(l1_loss & l2_push) | (l1_push & l2_loss)])
        
        # Full Loss: Loss on both
        prob_full_loss = np.sum(prob_matrix[l1_loss & l2_loss])
        
        # Calculate Fair Odds (Expected Return = 1)
        # Equation: Odds * (P_FullWin + 0.5 * P_HalfWin) = 1 - 0.5 * P_HalfWin - 0.5 * P_HalfLoss
        # Note: 1 - 0.5*P_HW - 0.5*P_HL is the expected return if you just get your stake back on pushes/half-losses?
        # Let's stick to the derived formula:
        # O = (1 - 0.5 * P_HalfWin - 0.5 * P_HalfLoss) / (P_FullWin + 0.5 * P_HalfWin)
        # Actually, simpler: Return from Half Loss is 0.5. Return from Half Win is 0.5(O+1).
        # 1 = P_FW*O + P_HW*0.5(O+1) + P_HL*0.5 + P_FL*0
        # 1 - 0.5*P_HW - 0.5*P_HL = O * (P_FW + 0.5*P_HW)
        
        numerator = 1.0 - 0.5 * prob_half_win - 0.5 * prob_half_loss # gemini
        denominator_home = prob_full_win + 0.5 * prob_half_win # gemini

        home_odds = numerator / denominator_home if denominator_home > 0 else 0
        
        # For Away Odds, the logic is symmetric but outcomes are reversed
        # Away Full Win = Home Full Loss
        # Away Half Win = Home Half Loss
        # Away Half Loss = Home Half Win
        # Away Full Loss = Home Full Win
        
        denominator_away = prob_full_loss + 0.5 * prob_half_loss # gemini

        away_odds = numerator / denominator_away if denominator_away > 0 else 0
        
        return {
            f"Home": home_odds,
            f"Away": away_odds,
            "details": {
                "Home Full Win": prob_full_win,
                "Home Half Win": prob_half_win,
                "Home Half Loss": prob_half_loss,
                "Home Full Loss": prob_full_loss
            }
        }
        
    else:
        # Integer or Half-ball handicap (e.g. -1.0, -1.5)
        # Treat as single line
        
        win = (goal_diff + handicap) > 0
        push = np.isclose(goal_diff + handicap, 0)
        loss = (goal_diff + handicap) < 0
        
        prob_win = np.sum(prob_matrix[win])
        prob_push = np.sum(prob_matrix[push])
        prob_loss = np.sum(prob_matrix[loss])
        
        # For 2-way odds with Push (Void), we usually calculate odds given a decisive result
        # i.e. P(Win) / (P(Win) + P(Loss))
        # Or simply 1 / P(Win) if we ignore push? 
        # In betting, if it's a push, money is returned. So the effective probability space is just Win vs Loss.
        
        effective_prob_home = prob_win / (prob_win + prob_loss) if (prob_win + prob_loss) > 0 else 0
        effective_prob_away = prob_loss / (prob_win + prob_loss) if (prob_win + prob_loss) > 0 else 0
        
        return {
            f"Home": 1.0 / effective_prob_home if effective_prob_home > 0 else 0,
            f"Away": 1.0 / effective_prob_away if effective_prob_away > 0 else 0,
            "details": {
                "Win": prob_win,
                "Push": prob_push,
                "Loss": prob_loss
            }
        }

def calculate_over_under_odds(total_goals, supremacy, threshold, max_goals=10, draw_factor=1.0):
    """
    Calculates fair odds for Over/Under (High/Low) markets, including quarter lines.
    
    Args:
        total_goals (float): Expected total goals (home + away).
        supremacy (float): Expected goal difference (home - away).
        threshold (float): The goal line (e.g., 2.5, 2.75, 3.0).
        draw_factor (float): Factor to adjust the probability of a draw. Default is 1.0.
        
    Returns:
        dict: Fair decimal odds for Over and Under.
    """
    home_avg_goals = (total_goals + supremacy) / 2.0
    away_avg_goals = (total_goals - supremacy) / 2.0
    
    # Ensure non-negative expectancies
    if home_avg_goals < 0: home_avg_goals = 0
    if away_avg_goals < 0: away_avg_goals = 0

    # Generate probability matrix
    goals_range = np.arange(max_goals + 1)
    home_probs = poisson.pmf(goals_range, home_avg_goals)
    away_probs = poisson.pmf(goals_range, away_avg_goals)
    prob_matrix = np.outer(home_probs, away_probs)
    
    # Apply draw factor
    if draw_factor != 1.0:
        diag_indices = np.diag_indices_from(prob_matrix)
        prob_matrix[diag_indices] *= draw_factor
        
    prob_matrix /= prob_matrix.sum()  # Normalize

    # Total goals matrix
    i, j = np.indices(prob_matrix.shape)
    total_goals = i + j
    
    # Check if it's a quarter line
    is_quarter = (abs(threshold) * 4) % 2 != 0
    
    if is_quarter:
        # Split into two lines
        line1 = threshold - 0.25
        line2 = threshold + 0.25
        
        # Calculate outcomes for "Over" bet
        # Line 1 (Lower): e.g. 2.5 for 2.75
        l1_over = total_goals > line1
        l1_push = np.isclose(total_goals, line1)
        l1_under = total_goals < line1
        
        # Line 2 (Higher): e.g. 3.0 for 2.75
        l2_over = total_goals > line2
        l2_push = np.isclose(total_goals, line2)
        l2_under = total_goals < line2
        
        # Over Analysis
        # Full Win: Over both
        prob_full_win = np.sum(prob_matrix[l1_over & l2_over])
        # Half Win: Over lower, Push higher
        prob_half_win = np.sum(prob_matrix[(l1_over & l2_push) | (l1_push & l2_over)])
        # Half Loss: Under higher, Push lower
        prob_half_loss = np.sum(prob_matrix[(l1_under & l2_push) | (l1_push & l2_under)])
        # Full Loss: Under both
        prob_full_loss = np.sum(prob_matrix[l1_under & l2_under])
        
        numerator = 1.0 - 0.5 * prob_half_win - 0.5 * prob_half_loss
        
        
        # Over Odds
        denominator_over = prob_full_win + 0.5 * prob_half_win
        # Under Odds
        denominator_under = prob_full_loss + 0.5 * prob_half_loss


        over_odds = numerator / denominator_over if denominator_over > 0 else 0
        
        under_odds = numerator / denominator_under if denominator_under > 0 else 0
        
        return {
            f"Over": over_odds,
            f"Under": under_odds,
            "details": {
                "Over Full Win": prob_full_win,
                "Over Half Win": prob_half_win,
                "Over Half Loss": prob_half_loss,
                "Over Full Loss": prob_full_loss
            }
        }
        
    else:
        # Integer or Half-ball line (e.g. 2.5, 3.0)
        over = total_goals > threshold
        push = np.isclose(total_goals, threshold)
        under = total_goals < threshold
        
        prob_over = np.sum(prob_matrix[over])
        prob_push = np.sum(prob_matrix[push])
        prob_under = np.sum(prob_matrix[under])
        
        # Effective probability (excluding push)
        effective_prob_over = prob_over / (prob_over + prob_under) if (prob_over + prob_under) > 0 else 0
        effective_prob_under = prob_under / (prob_over + prob_under) if (prob_over + prob_under) > 0 else 0
        
        return {
            f"Over": 1.0 / effective_prob_over if effective_prob_over > 0 else 0,
            f"Under": 1.0 / effective_prob_under if effective_prob_under > 0 else 0,
            "details": {
                "Over": prob_over,
                "Push": prob_push,
                "Under": prob_under
            }
        }


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

if __name__ == "__main__":
    # Example: Team A (Home) expects to score 1.7 goals
    #          Team B (Away) expects to score 1.2 goals
    # These values are usually derived from historical data (Attack/Defense strength)
    # home_expectancy = 1.52625
    # away_expectancy = 1.39195
    
    # total_goals = home_expectancy + away_expectancy
    # supremacy = home_expectancy - away_expectancy

    market_data = {
        # 1X2 Odds (Home, Draw, Away)
        '1x2': {'Home': 1.44, 'Draw': 4.45, 'Away': 4.80},
        
        # Asian Handicap (Line, Home Odds, Away Odds)
        'handicap': {
            'line': -1.25, 
            'odds': {'Home': 1.94, 'Away': 1.94}
        },
        
        # Over/Under (Line, Over Odds, Under Odds)
        'ou': {
            'line': 2.5, 
            'odds': {'Over': 1.83, 'Under': 2.05}
        },
        
        # Handicap HAD (Line, Home, Draw, Away Odds)
        'hhad': {
            'line': -1.0,
            'odds': {'Home': 2.22, 'Draw': 3.80, 'Away': 2.35}
        },
        
        # Correct Score (Score: Odds)
        'crs': {
            '1-0': 11.0,
            '2-0': 9.25,
            '2-1': 7.0,
            '0-0': 23.0,
            '1-1': 9.25,
            '0-1': 23.0,
            '0-2': 30.0,
            '1-2': 13.5,
        },
        
        # Total Goals
        'tgl': {
            'count_plus_threshold': 7,
            'odds': {
                '0': 23.0,
                '1': 7.5,
                '2': 4.40,
                '3': 3.80,
                '4': 4.1,
                '5': 5.7,
                '6': 8.75,
                '7+': 10.5
            }
        },
        
        # Odd/Even (Odd, Even Odds)
        'oe': {'Odd': 1.86, 'Even': 1.84},
        
        # First Half 1X2 (HAD)
        'fh_1x2': {'Home': 1.8, 'Draw': 2.68, 'Away': 4.75},
        
        # First Half Asian Handicap
        'fh_handicap': {
            'line': -0.5, 
            'odds': {'Home': 1.95, 'Away': 1.93}
        },
        
        # First Half Over/Under (HILO)
        'fh_ou': {
            'line': 1.0, 
            'odds': {'Over': 1.76, 'Under': 2.13}
        },
        
        # First Half Correct Score (CRS)
        'fh_crs': {
            '1-0': 3.8,
            '2-0': 6.4,
            '2-1': 9.75,
            '0-0': 3.85,
            '1-1': 6.4,
            '0-1': 7.5,
            '0-2': 19.0,
            '1-2': 18.0,
        },
        
        # Half-Time / Full-Time
        'ht_ft': {
            'Home/Home': 2.06,
            'Home/Draw': 14.0,
            'Home/Away': 30.0,
            'Draw/Home': 4.35,
            'Draw/Draw': 8.0,
            'Draw/Away': 11.5,
            'Away/Home': 17.0,
            'Away/Draw': 14.0,
            'Away/Away': 7.5
        }
    }

    total_goals = 2.8772
    supremacy = 1.4231
    draw_factor = 1.0
    fh_factor = 0.56

    # total_goals_fh = 1.2343
    # supremacy_fh = 0.5120

    # --- Verification ---
    print("\n" + "="*40)
    print("Verification: Model Output vs Target (Normalized)")
    print("="*40)
    
    # 1X2
    if '1x2' in market_data:
        target_1x2 = odds_to_probs(market_data['1x2'])
        model_1x2 = calculate_match_probabilities(total_goals, supremacy, draw_factor=draw_factor)
        print(f"1X2 Home: Model {model_1x2['Home Win']:.2%} vs Target {target_1x2['Home']:.2%}")
        print(f"1X2 Draw: Model {model_1x2['Draw']:.2%} vs Target {target_1x2['Draw']:.2%}")
        print(f"1X2 Away: Model {model_1x2['Away Win']:.2%} vs Target {target_1x2['Away']:.2%}")
        
    # Handicap
    if 'handicap' in market_data:
        h_line = market_data['handicap']['line']
        target_h = odds_to_probs(market_data['handicap']['odds'])
        model_h_odds = calculate_asian_handicap_odds(total_goals, supremacy, h_line, draw_factor=draw_factor)
        model_h_prob = 1 / model_h_odds[f"Home"]
        print(f"Handicap Home ({h_line}): Model {model_h_prob:.2%} vs Target {target_h['Home']:.2%}")
        
    # O/U
    if 'ou' in market_data:
        ou_line = market_data['ou']['line']
        target_ou = odds_to_probs(market_data['ou']['odds'])
        model_ou_odds = calculate_over_under_odds(total_goals, supremacy, ou_line, draw_factor=draw_factor)
        model_ou_prob = 1 / model_ou_odds[f"Over"]
        print(f"Over {ou_line}: Model {model_ou_prob:.2%} vs Target {target_ou['Over']:.2%}")

    # Handicap HAD
    if 'hhad' in market_data:
        hhad_line = market_data['hhad']['line']
        target_hhad = odds_to_probs(market_data['hhad']['odds'])
        model_hhad_odds = calculate_handicap_had_odds(total_goals, supremacy, hhad_line, draw_factor=draw_factor)
        print(f"HHAD Home ({hhad_line}): Model {1/model_hhad_odds['Home']:.2%} vs Target {target_hhad['Home']:.2%}")
        print(f"HHAD Draw ({hhad_line}): Model {1/model_hhad_odds['Draw']:.2%} vs Target {target_hhad['Draw']:.2%}")
        print(f"HHAD Away ({hhad_line}): Model {1/model_hhad_odds['Away']:.2%} vs Target {target_hhad['Away']:.2%}")

    # Correct Score
    if 'crs' in market_data:
        target_crs = odds_to_probs(market_data['crs'])
        model_crs_odds = calculate_correct_score_odds(total_goals, supremacy, draw_factor=draw_factor)
        for score in target_crs:
            print(f"CRS {score}: Model {model_crs_odds['details'].get(score, 0):.2%} vs Target {target_crs[score]:.2%}")

    # Total Goals
    if 'tgl' in market_data:
        tgl_data = market_data['tgl']
        threshold = tgl_data.get('count_plus_threshold')
        target_tgl = odds_to_probs(tgl_data['odds'])
        model_tgl_odds = calculate_total_goals_odds(total_goals, supremacy, count_plus_threshold=threshold, draw_factor=draw_factor)
        for tgl_key in target_tgl:
            print(f"Total Goals {tgl_key}: Model {model_tgl_odds['details'].get(tgl_key, 0):.2%} vs Target {target_tgl[tgl_key]:.2%}")

    # Odd/Even
    if 'oe' in market_data:
        target_oe = odds_to_probs(market_data['oe'])
        model_oe_odds = calculate_odd_even_odds(total_goals, supremacy, draw_factor=draw_factor)
        print(f"Odd: Model {model_oe_odds['details']['Odd']:.2%} vs Target {target_oe['Odd']:.2%}")
        print(f"Even: Model {model_oe_odds['details']['Even']:.2%} vs Target {target_oe['Even']:.2%}")

    # First Half Verification
    fh_total_goals = total_goals * fh_factor
    fh_supremacy = supremacy * fh_factor
    
    if 'fh_1x2' in market_data:
        target_fh_1x2 = odds_to_probs(market_data['fh_1x2'])
        model_fh_1x2 = calculate_match_probabilities(fh_total_goals, fh_supremacy, draw_factor=draw_factor)
        print(f"FH 1X2 Home: Model {model_fh_1x2['Home Win']:.2%} vs Target {target_fh_1x2['Home']:.2%}")
        print(f"FH 1X2 Draw: Model {model_fh_1x2['Draw']:.2%} vs Target {target_fh_1x2['Draw']:.2%}")
        print(f"FH 1X2 Away: Model {model_fh_1x2['Away Win']:.2%} vs Target {target_fh_1x2['Away']:.2%}")
        
    if 'fh_handicap' in market_data:
        fh_h_line = market_data['fh_handicap']['line']
        target_fh_h = odds_to_probs(market_data['fh_handicap']['odds'])
        model_fh_h_odds = calculate_asian_handicap_odds(fh_total_goals, fh_supremacy, fh_h_line, draw_factor=draw_factor)
        model_fh_h_prob_home = 1 / model_fh_h_odds["Home"] if model_fh_h_odds.get("Home", 0) > 0 else 0
        print(f"FH Handicap Home ({fh_h_line}): Model {model_fh_h_prob_home:.2%} vs Target {target_fh_h['Home']:.2%}")
        
    if 'fh_ou' in market_data:
        fh_ou_line = market_data['fh_ou']['line']
        target_fh_ou = odds_to_probs(market_data['fh_ou']['odds'])
        model_fh_ou_odds = calculate_over_under_odds(fh_total_goals, fh_supremacy, fh_ou_line, draw_factor=draw_factor)
        model_fh_ou_prob_over = 1 / model_fh_ou_odds["Over"] if model_fh_ou_odds.get("Over", 0) > 0 else 0
        print(f"FH Over {fh_ou_line}: Model {model_fh_ou_prob_over:.2%} vs Target {target_fh_ou['Over']:.2%}")
        
    if 'fh_crs' in market_data:
        target_fh_crs = odds_to_probs(market_data['fh_crs'])
        model_fh_crs_odds = calculate_correct_score_odds(fh_total_goals, fh_supremacy, draw_factor=draw_factor)
        for score in target_fh_crs:
            print(f"FH CRS {score}: Model {model_fh_crs_odds['details'].get(score, 0):.2%} vs Target {target_fh_crs[score]:.2%}")
            
