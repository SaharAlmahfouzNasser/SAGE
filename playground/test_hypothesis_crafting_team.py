import asyncio

from autogen_agentchat.agents import UserProxyAgent
from autogen_agentchat.conditions import TextMentionTermination
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.ui import Console
from HypothesisGeneration.agents import create_hypothesis_expansion_agent, create_novelty_critic_agent, create_scientist
from HypothesisGeneration.modifiedAutogen.console import CustomConsole

test_message = """
### Knowledge Graph path:
radiotherapy||Treatment -- increased -- incidence of secondary primary bladder cancer (SPBC)||Disease -- increased -- chemotherapy||Drug -- sensitivity to -- bladder cancer||Disease -- is associated with -- ADRM1||Protein -- presents significant prognostic value in -- male||Process.

### Biomedical Definitions:
**Radiotherapy:** A cancer treatment modality that uses high-energy radiation, such as X-rays, gamma rays, or charged particles, to induce DNA damage in tumor cells, leading to cell death or growth arrest. It is commonly employed in the management of various malignancies, including bladder cancer, either as a primary or adjunct therapy.

### Healthcare Relationships:
**Radiotherapy -- received -- Patients:** Patients with bladder cancer often undergo radiotherapy as part of their treatment regimen. The effectiveness and side effects of radiotherapy are influenced by tumor biology and patient-specific factors, including molecular markers.

**Patients -- undergoing investigation for -- Bladder Cancer:** The patient cohort is being studied to understand disease characteristics, treatment responses, and prognostic factors, including molecular markers like ADRM1.

**Bladder Cancer -- is associated with -- ADRM1:** The expression or mutation status of ADRM1 is linked to bladder cancer pathogenesis and progression. Its association suggests that ADRM1 may serve as a biomarker for disease prognosis or a potential therapeutic target.

**ADRM1 -- presents significant prognostic value in -- Male:** The expression level or activity of ADRM1 has been found to have a notable correlation with clinical outcomes specifically in male patients, indicating sex-specific differences in molecular pathways or disease behavior.

"""


async def main():
    # Start the team chat with the input message and a console UI to display the conversation.
    # Define a termination condition that stops the task if the critic approves.
    scientist = create_scientist()
    novelty_critic_agent = create_novelty_critic_agent()
    hypothesis_expansion_agent = create_hypothesis_expansion_agent()
    user = UserProxyAgent(name="user")
    text_termination_hypothesis = TextMentionTermination("The hypothesis is approved.")
    user_termination = TextMentionTermination("Approved", sources=["user"])

    # Create a team with the primary and critic agents.
    team = RoundRobinGroupChat(
        [
            RoundRobinGroupChat(
                [scientist, hypothesis_expansion_agent, novelty_critic_agent],
                termination_condition=text_termination_hypothesis,
                max_turns=12,
            ),
            user,
        ],
        termination_condition=user_termination,
        max_turns=6,
        name="hypothesis_team",
    )
    await team.reset()  # Reset the team for the next run.
    await CustomConsole(
        team.run_stream(task=test_message), output_stats=True, file_path="../outputs/test_hypothesis_crafting_team.txt"
    )


if __name__ == "__main__":
    asyncio.run(main())
