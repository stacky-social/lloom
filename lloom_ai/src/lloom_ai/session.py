# Concept induction session functions
# =================================================

# Imports
import time
import pandas as pd
import ipywidgets as widgets
import random

# TODO(MSL): check on relative imports in package
# Local imports
if __package__ is None or __package__ == '':
    # uses current directory visibility
    from concept_induction import *
    from concept import Concept
else:
    # uses current package visibility
    from .concept_induction import *
    from .concept import Concept

# SESSION class ================================
class Session:
    def __init__(
        self,
        in_df: pd.DataFrame,
        doc_id_col: str,
        doc_col: str,
        save_path: str = None,
        debug: bool = False,
    ):
        # Settings
        self.model_name = "gpt-3.5-turbo"
        self.synth_model_name = "gpt-4-turbo-preview"
        self.use_base_api = True
        self.debug = debug  # Whether to run in debug mode

        if save_path is None:
            # Automatically set using timestamp
            t = time.strftime('%Y-%m-%d-%H-%M-%S', time.localtime())
            save_path = f"./exports/{t}"
        self.save_path = save_path

        # Input data
        self.in_df = in_df
        self.doc_id_col = doc_id_col
        self.doc_col = doc_col
        self.df_to_score = in_df  # Default to in_df for concept scoring

        # Output data
        self.saved_dfs = {}  # maps from (step_name, time_str) to df
        self.concepts = {}  # maps from concept_id to Concept 
        self.results = {}  # maps from concept_id to its score_df
        self.df_filtered = None  # Current quotes df
        self.df_bullets = None  # Current bullet points df
        self.select_widget = None  # Widget for selecting concepts
        
        # Cost/Time tracking
        self.time = {}  # Stores time required for each step
        self.cost = []  # Stores result of cost estimation
        self.tokens = {
            "in_tokens": [],
            "out_tokens": [],
        }
    
    def save(self):
        # Saves current session to file
        select_widget = self.select_widget
        self.select_widget = None  # Remove widget before saving (can't be pickled)

        t = time.strftime('%Y-%m-%d-%H-%M-%S', time.localtime())
        cur_path = f"{self.save_path}__{t}.pkl"
        with open(cur_path, "wb") as f:
            pickle.dump(self, f)
        print(f"Saved session to {cur_path}")

        self.select_widget = select_widget  # Restore widget after saving

    def get_save_key(self, step_name):
        t = time.strftime('%Y-%m-%d-%H-%M-%S', time.localtime())
        k = (step_name, t)  # Key of step name and current time
        return k
    
    def summary(self):
        # Time
        total_time = np.sum(list(self.time.values()))
        print(f"Total time: {total_time:0.2f} sec ({(total_time/60):0.2f} min)")
        for step_name, time in self.time.items():
            print(f"\t{step_name}: {time:0.2f} sec")

        # Cost
        total_cost = np.sum(self.cost)
        print(f"\n\nTotal cost: {total_cost:0.2f}")

        # Tokens
        in_tokens = np.sum(self.tokens["in_tokens"])
        out_tokens = np.sum(self.tokens["out_tokens"])
        total_tokens =  in_tokens + out_tokens
        print(f"\n\nTokens: total={total_tokens}, in={in_tokens}, out={out_tokens}")

    def show_selected(self):
        active_concepts = self.__get_active_concepts()
        print(f"Active concepts (n={len(active_concepts)}):")
        for c_id, c in active_concepts.items():
            print(f"- {c.name}: {c.prompt}")

    # HELPER FUNCTIONS ================================
    async def gen(self, seed=None, args=None, debug=True):
        # TODO: modify to automatically determine args
        if args is None:
            args = {
                "filter_n_quotes": 2,
                "summ_n_bullets": "2-4",
                "cluster_batch_size": 20,
                "synth_n_concepts": 10,
            }

        # Run concept generation
        df_filtered = await distill_filter(
            text_df=self.in_df, 
            doc_col=self.doc_col,
            doc_id_col=self.doc_id_col,
            model_name=self.model_name,
            n_quotes=args["filter_n_quotes"],
            seed=seed,
            sess=self,
        )
        self.df_to_score = df_filtered
        self.df_filtered = df_filtered
        if debug:
            print("df_filtered")
            display(df_filtered)
        
        df_bullets = await distill_summarize(
            text_df=df_filtered, 
            doc_col=self.doc_col,
            doc_id_col=self.doc_id_col,
            model_name=self.model_name,
            n_bullets=args["summ_n_bullets"],
            seed=seed,
            sess=self,
        )
        self.df_bullets = df_bullets
        if debug:
            print("df_bullets")
            display(df_bullets)

        df_cluster = await cluster(
            text_df=df_bullets, 
            doc_col=self.doc_col,
            doc_id_col=self.doc_id_col,
            batch_size=args["cluster_batch_size"],
            sess=self,
        )
        if debug:
            print("df_cluster")
            display(df_cluster)
        
        df_concepts = await synthesize(
            cluster_df=df_cluster, 
            doc_col=self.doc_col,
            doc_id_col=self.doc_id_col,
            model_name=self.synth_model_name,
            n_concepts=args["synth_n_concepts"],
            pattern_phrase="unique topic",
            seed=seed,
            sess=self,
        )
        if debug:
            # Print results
            print("synthesize")
            for k, c in self.concepts.items():
                print(f'- Concept {k}:\n\t{c.name}\n\t- Prompt: {c.prompt}')

    def __concepts_to_json(self):
        concept_dict = {c_id: c.to_dict() for c_id, c in self.concepts.items()}
        # Get examples from example IDs
        for c_id, c in concept_dict.items():
            ex_ids = c["example_ids"]
            in_df = self.df_filtered.copy()
            in_df[self.doc_id_col] = in_df[self.doc_id_col].astype(str)
            examples = in_df[in_df[self.doc_id_col].isin(ex_ids)][self.doc_col].tolist()
            c["examples"] = examples
        return json.dumps(concept_dict)
    
    def select(self):
        concepts_json = self.__concepts_to_json()
        w = get_select_widget(concepts_json)
        self.select_widget = w
        return w

    def __get_active_concepts(self):
        # Update based on widget
        if self.select_widget is not None:
            widget_data = json.loads(self.select_widget.data)
            for c_id, c in self.concepts.items():
                widget_active = widget_data[c_id]["active"]
                c.active = widget_active
        return {c_id: c for c_id, c in self.concepts.items() if c.active}

    # Score the specified concepts
    # Only score the concepts that are active
    async def score(self, c_ids=None, get_highlights=True):
        concepts = {}
        active_concepts = self.__get_active_concepts()
        if c_ids is None:
            # Score all active concepts
            for c_id, c in active_concepts.items():
                concepts[c_id] = c
        else:
            # Score only the specified concepts
            for c_id in c_ids:
                if c_id in active_concepts:
                    concepts[c_id] = active_concepts[c_id]
        
        # Run usual scoring; results are stored to self.results within the function
        score_df = await score_concepts(
            text_df=self.df_to_score, 
            text_col=self.doc_col, 
            doc_id_col=self.doc_id_col,
            concepts=concepts,
            get_highlights=get_highlights,
            sess=self,
        )

        return score_df

    def __get_concept_from_name(self, name):
        if name == "Outlier":
            return Concept(name="Outlier", prompt=OUTLIER_CRITERIA, example_ids=[], active=True)
        for c_id, c in self.concepts.items():
            if c.name == name:
                return c
        return None
    
    def get_score_df(self):
        active_concepts = self.__get_active_concepts()
        active_score_dfs = [self.results[c_id] for c_id in active_concepts.keys() if c_id in self.results]
        score_df = pd.concat(active_score_dfs)
        score_df = score_df.rename(columns={"doc_id": self.doc_id_col})
        return score_df

    def __get_concept_highlights(self, c, threshold=0.75, highlight_col="highlight", lim=3):
        if c.name == "Outlier":
            return []
        if c.id not in self.results:
            return []
        score_df = self.results[c.id].copy()
        score_df = score_df[score_df["score"] > threshold]
        highlights = score_df[highlight_col].tolist()
        # shuffle highlights
        random.shuffle(highlights)
        if lim is not None:
            highlights = highlights[:lim]
        return highlights

    def __get_rep_examples(self, c):
        if c.name == "Outlier":
            return []
        if c.id not in self.results:
            return []
        df = self.df_filtered.copy()
        df[self.doc_id_col] = df[self.doc_id_col].astype(str)
        ex_ids = c.example_ids
        ex = df[df[self.doc_id_col].isin(ex_ids)][self.doc_col].tolist()
        return ex

    def __get_df_for_export(self, item_df, threshold=0.75, include_outliers=False):
        # Prepares a dataframe meant for exporting the current session results
        # Includes concept, criteria, summary, representative examples, prevalence, and highlights
        matched = item_df[(item_df.concept_score_orig > threshold)]
        if not include_outliers:
            matched = matched[item_df.concept != "Outlier"]

        df = matched.groupby(by=["id", "concept"]).count().reset_index()[["concept", self.doc_col]]
        concepts = [self.__get_concept_from_name(c_name) for c_name in df.concept.tolist()]
        df["criteria"] = [c.prompt for c in concepts]
        df["summary"] = [c.summary for c in concepts]
        df["rep_examples"] = [self.__get_rep_examples(c) for c in concepts]
        df["highlights"] = [self.__get_concept_highlights(c, threshold) for c in concepts]
        df = df.rename(columns={self.doc_col: "n_matches"})
        df["prevalence"] = np.round(df["n_matches"] / len(self.in_df), 2)
        df = df[["concept", "criteria", "summary", "rep_examples", "prevalence", "highlights"]]
        return df
        

    def vis(self, cols_to_show=[], custom_groups={}, show_highlights=True, norm_by="concept", export_df=False):
        active_concepts = self.__get_active_concepts()
        score_df = self.get_score_df()

        widget, matrix_df, item_df, item_df_wide = visualize(
            in_df=self.in_df,
            score_df=score_df,
            doc_col=self.doc_col,
            doc_id_col=self.doc_id_col,
            score_col="score",
            df_filtered=self.df_filtered,
            df_bullets=self.df_bullets,
            concepts=active_concepts,
            cols_to_show=cols_to_show,
            custom_groups=custom_groups,
            show_highlights=show_highlights,
            norm_by=norm_by,
        )
        if export_df:
            return self.__get_df_for_export(item_df)
        
        return widget

    def export_df(self):
        return self.vis(export_df=True)

    async def add(self, name, prompt, ex_ids=[], get_highlights=True):
        # Add concept
        c = Concept(name=name, prompt=prompt, example_ids=ex_ids, active=True)
        self.concepts[c.id] = c

        # Update widget
        self.select_widget = self.select()

        # Run scoring
        cur_score_df = await self.score(c_ids=[c.id], get_highlights=get_highlights)
        
        # Store results
        self.results[c.id] = cur_score_df
    
    
    async def edit(self):
        raise NotImplementedError("Edit function not yet implemented")