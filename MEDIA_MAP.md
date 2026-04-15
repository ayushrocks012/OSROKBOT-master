# OSROKBOT Media Map

This map records every active image template under `Media/`. The primary scan
source is `Classes/action_sets.py`; action-internal references and runner-level
global blockers are included so active assets are not archived accidentally.

| FileName | Workflow | Purpose |
| --- | --- | --- |
| `applus.png` | `loharjr` | Detects the action-point add button during AP refill recovery. |
| `apuse.png` | `loharjr` | Detects the AP use button after opening the AP refill dialog. |
| `armyc.png` | `loharjr`, `farm_gold` | Detects the army/march status UI before continuing combat or gold gathering checks. |
| `arrow.png` | `farm_barb`, `farm_barb_all`, `loharjrt` | Selects the found target marker before attack or rally actions. |
| `attackaction.png` | `farm_barb`, `farm_barb_all`, `loharjr` | Confirms that an attack action is available on the selected target. |
| `barbland.png` | `farm_barb`, `farm_barb_all` | Opens the barbarian search category from the search panel. |
| `barbreport.png` | `scout_explore` | Detects a barbarian scout report row. |
| `barbreport2.png` | `scout_explore` | Detects an alternate barbarian scout report row. |
| `captchachest.png` | `email_captcha` | Watches for captcha chest UI before notifying and pausing. |
| `cavereport.png` | `scout_explore` | Detects a cave scout report row. |
| `confirm.png` | `global_blocker` | Runner-level blocker dismissed before workflow actions execute. |
| `cornicon.png` | `farm_rss`, `farm_rss_new`, `farm_food` | Selects food resource search. |
| `defeat.png` | `loharjr` | Detects defeat after combat resolution. |
| `escx.png` | `farm_rss`, `farm_rss_new`, `farm_wood`, `farm_food`, `farm_stone`, `farm_gold`, `global_blocker` | Closes escape/modal overlays and acts as a runner-level blocker. |
| `explorationreport.png` | `scout_explore` | Detects the exploration report tab. |
| `explorationreportactive.png` | `scout_explore` | Detects the active exploration report tab. |
| `explore.png` | `scout_explore` | Detects the day scout explore button. |
| `exploreaction.png` | `scout_explore` | Detects the explore action button on fog/scout targets. |
| `exploreicon.png` | `scout_explore` | Detects the scout explore icon after report recovery. |
| `explorenight.png` | `scout_explore` | Detects the night scout explore button. |
| `ficon.png` | `farm_rss`, `farm_rss_new`, `farm_wood`, `farm_food`, `farm_stone`, `farm_gold`, `farm_gems`, `loharjr` | Confirms map/search view before resource or world-object workflows continue. |
| `gatheraction.png` | `farm_rss`, `farm_wood`, `farm_food`, `farm_stone`, `farm_gold` | Detects the gather action on a selected resource node. |
| `gemdepo.png` | `farm_gems` | Detects a gem deposit variant during map scanning. |
| `gemdepo1.png` | `farm_gems` | Detects a second gem deposit variant during map scanning. |
| `gemdepo2.png` | `farm_gems` | Detects a third gem deposit variant during map scanning. |
| `goldicon.png` | `farm_rss`, `farm_rss_new`, `farm_gold` | Selects gold resource search. |
| `holyreport.png` | `scout_explore` | Detects a holy-site scout report row. |
| `investigateaction.png` | `scout_explore` | Detects the investigate button for cave targets. |
| `logicon.png` | `farm_rss`, `farm_rss_new`, `farm_wood` | Selects wood resource search. |
| `lohar.png` | `farm_barb` | Finds an existing Lohar troop option before marching. |
| `loharjr.png` | `loharjrt` | Detects the Lohar Jr inventory item before use. |
| `marauder.png` | `loharjr` | Detects marauder targets during world-map scanning. |
| `marchaction.png` | `farm_barb`, `farm_barb_all`, `farm_rss`, `farm_rss_new`, `farm_wood`, `farm_food`, `farm_stone`, `farm_gold`, `loharjr`, `loharjrt` | Detects the standard march button. |
| `mult.png` | `train_troops` | Detects the multi-use speedup control. |
| `newtroopaction.png` | `farm_barb`, `farm_barb_all`, `farm_rss`, `farm_rss_new`, `farm_wood`, `farm_food`, `farm_stone`, `farm_gold`, `loharjr` | Detects the new troop button before march setup. |
| `passreport.png` | `scout_explore` | Detects a pass scout report row. |
| `rallyaction.png` | `loharjrt` | Detects the rally action for Lohar Jr. |
| `rallysmallaction.png` | `loharjrt` | Detects the small rally confirmation button. |
| `reportbanner.png` | `scout_explore` | Checks whether a scout report still has an unexplored banner. |
| `scoutticon.png` | `scout_explore` | Detects the scout icon when recovering from report state. |
| `searchaction.png` | `farm_barb`, `farm_barb_all`, `farm_rss`, `farm_rss_new`, `farm_wood`, `farm_food`, `farm_stone`, `farm_gold` | Detects the search button after selecting a target/resource category. |
| `sendaction.png` | `scout_explore` | Detects the send button for scout dispatch. |
| `smallmarchaction.png` | `farm_barb`, `farm_barb_all`, `farm_rss`, `farm_rss_new`, `farm_wood`, `farm_food`, `farm_stone`, `farm_gold`, `loharjr` | Detects the compact march button variant. |
| `speedupicon.png` | `train_troops` | Detects available training speedups. |
| `stable.png` | `train_troops` | Opens or detects the stable building/training entry point. |
| `stoneicon.png` | `farm_rss`, `farm_rss_new`, `farm_stone` | Selects stone resource search. |
| `t1cav.png` | `train_troops` | Selects T1 cavalry. |
| `trainbutton.png` | `train_troops` | Detects the final train button. |
| `trainhorse.png` | `train_troops` | Detects cavalry training UI. |
| `upgrade.png` | `train_troops` | Detects the upgrade/training tab control. |
| `upgradeaction.png` | `train_troops` | Detects the upgrade action before training. |
| `useaction.png` | `loharjrt` | Detects the use button for the Lohar Jr inventory item. |
| `victory.png` | `farm_barb`, `farm_barb_all`, `loharjr` | Detects successful combat resolution. |
| `villagereport.png` | `scout_explore` | Detects a village scout report row. |

## Archive Status

No additional root-level `Media/*.png` files were unused at the time of this
audit. Existing archived templates remain under `Media/Legacy/`.
